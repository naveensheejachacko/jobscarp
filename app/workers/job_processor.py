"""Orchestrates the full pipeline: fetch -> extract -> normalize -> dedupe ->
score -> AI-analyze -> generate messages for high scorers -> persist -> sync
Sheets -> notify. Runs on a schedule (APScheduler) and is also runnable once
from the CLI for manual/testing runs. Never submits an application anywhere.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session

from app.config import CandidateProfile, Settings, get_profile, get_settings
from app.db import get_session_factory
from app.models.job import Job
from app.schemas.job import ExtractedJob, ExtractionFailure, RawEmail
from app.services.ai import BaseLLMProvider, get_llm_provider, safe_analyze_job, safe_generate_message
from app.services.deduplicator import find_duplicate
from app.services.extractor import extract_job
from app.services.gmail import EmailFetcher, GmailClient
from app.services.matcher import score_job
from app.services.normalizer import normalize_job
from app.services.notifier import BaseNotifier, EmailNotifier

logger = logging.getLogger(__name__)

MESSAGE_GENERATION_THRESHOLD = 80


def _sources(settings: Settings) -> dict[str, str]:
    return {
        "cutshort": settings.gmail_query_cutshort,
        "instahyre": settings.gmail_query_instahyre,
    }


def process_one_email(
    raw: RawEmail,
    db: Session,
    profile: CandidateProfile,
    llm_provider: BaseLLMProvider | None,
) -> Job | None:
    """Extract -> normalize -> dedupe -> score -> (optionally) AI-analyze/message
    one already-fetched email. Returns the created/updated Job, or None if
    extraction failed (caller records the failure via EmailFetcher.mark_processed)."""
    extracted = extract_job(raw)
    if isinstance(extracted, ExtractionFailure):
        logger.info("pipeline.extraction_failed", extra={"reason": extracted.reason})
        return None

    normalized = normalize_job(extracted)
    existing = find_duplicate(db, normalized)

    match_result = score_job(normalized, profile)

    if existing is not None:
        job = existing
        job.job_description = normalized.job_description or job.job_description
        job.job_url = job.job_url or normalized.job_url
    else:
        job = Job(company=normalized.company, job_title=normalized.job_title, source=normalized.source)
        db.add(job)

    job.job_url = job.job_url or normalized.job_url
    job.location = normalized.location
    job.work_mode = normalized.work_mode
    job.salary_min = normalized.salary_min
    job.salary_max = normalized.salary_max
    job.currency = normalized.currency
    job.experience_min = normalized.experience_min
    job.experience_max = normalized.experience_max
    job.required_skills = normalized.required_skills
    job.preferred_skills = normalized.preferred_skills
    job.job_description = normalized.job_description
    job.received_at = normalized.received_at
    job.recruiter_name = normalized.recruiter_name
    job.recruiter_email = normalized.recruiter_email

    job.match_score = match_result.match_score
    job.priority = match_result.priority
    job.match_reason = match_result.match_reason
    job.missing_skills = match_result.missing_skills

    if llm_provider is not None:
        analysis = safe_analyze_job(llm_provider, normalized, profile)
        if analysis is not None:
            job.red_flags = analysis.red_flags
            if analysis.why_good_fit:
                job.match_reason = f"{job.match_reason} | AI: {analysis.why_good_fit}"

        if match_result.match_score >= MESSAGE_GENERATION_THRESHOLD:
            matched = [s for s in normalized.required_skills if s.lower() in profile.all_skills_lower]
            message = safe_generate_message(llm_provider, normalized, profile, matched)
            if message:
                job.application_message = message

    db.flush()
    return job


_UNSET = object()  # distinguishes "not passed, auto-construct" from an explicit None (LLM disabled)


def run_once(
    session_factory=None,
    gmail_client: GmailClient | None = None,
    llm_provider: BaseLLMProvider | None = _UNSET,  # type: ignore[assignment]
    notifier: BaseNotifier | None = None,
    sheets_sync: bool = True,
) -> list[Job]:
    """Runs one full pipeline pass. All dependencies are injectable so this can
    be exercised in tests without real Gmail/LLM/Sheets access.

    llm_provider defaults to auto-constructing from Settings (production/scheduler
    use); pass explicit None to run with deterministic scoring only and no LLM
    calls at all (used by tests, and as the automatic fallback when the
    configured provider can't be constructed)."""
    settings = get_settings()
    profile = get_profile()
    session_factory = session_factory or get_session_factory()

    if gmail_client is None:
        gmail_client = GmailClient(settings.gmail_credentials_path, settings.gmail_token_path)
    if llm_provider is _UNSET:
        try:
            llm_provider = get_llm_provider(settings)
        except Exception as exc:  # noqa: BLE001 — missing/invalid LLM config must not stop ingestion
            logger.warning("pipeline.llm_unavailable", extra={"error": str(exc)})
            llm_provider = None
    if notifier is None:
        notifier = EmailNotifier(gmail_client, settings.notify_email_to)

    fetcher = EmailFetcher(gmail_client, session_factory)
    processed_jobs: list[Job] = []

    with session_factory() as db:
        for source, query in _sources(settings).items():
            for raw in fetcher.fetch_new(query, source):
                extracted = extract_job(raw)
                if isinstance(extracted, ExtractionFailure):
                    fetcher.mark_processed(
                        raw.message_id, source,
                        extraction_failed=True,
                        raw_subject=raw.subject, raw_sender=raw.sender, raw_snippet=raw.snippet,
                    )
                    continue

                job = process_one_email(raw, db, profile, llm_provider)
                db.commit()
                if job is not None:
                    fetcher.mark_processed(raw.message_id, source, job_id=job.id)
                    processed_jobs.append(job)
                    if job.match_score is not None and job.match_score >= settings.notify_min_score:
                        try:
                            notifier.notify(job)
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("pipeline.notify_failed", extra={"error": str(exc)})

        if sheets_sync and settings.google_sheet_id:
            _sync_sheets(db, gmail_client, settings)

    return processed_jobs


def _sync_sheets(db: Session, gmail_client: GmailClient, settings: Settings) -> None:
    try:
        from app.services.sheets import GoogleSheetsClient, sync_jobs_to_sheet

        creds = gmail_client._load_credentials()  # same OAuth creds, sheets scope included
        sheets_client = GoogleSheetsClient(creds, settings.google_sheet_id)
        all_jobs = db.query(Job).all()
        sync_jobs_to_sheet(sheets_client, all_jobs)
    except Exception as exc:  # noqa: BLE001 — Sheets sync failure must not break ingestion
        logger.warning("pipeline.sheets_sync_failed", extra={"error": str(exc)})


def start_scheduler() -> BackgroundScheduler:
    settings = get_settings()
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        run_once,
        "interval",
        minutes=settings.process_interval_minutes,
        id="job_pipeline",
        next_run_time=None,  # don't fire immediately on startup; wait one full interval
    )
    scheduler.start()
    logger.info("scheduler.started", extra={"interval_minutes": settings.process_interval_minutes})
    return scheduler


def stop_scheduler(scheduler: BackgroundScheduler | None) -> None:
    if scheduler is not None:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    import sys

    if "--once" in sys.argv:
        jobs = run_once()
        print(f"Processed {len(jobs)} job(s).")
        for job in jobs:
            print(f"  [{job.match_score}] {job.job_title} @ {job.company} ({job.priority.value if job.priority else '?'})")
    else:
        print("Usage: python -m app.workers.job_processor --once")
