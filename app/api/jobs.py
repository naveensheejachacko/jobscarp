"""GET/POST/PATCH endpoints for browsing, re-analyzing, and manually progressing
jobs. Nothing here ever submits an application anywhere — status transitions
are user-driven records only, per the product requirement that the final apply
decision stays manual.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import Settings, get_profile, get_settings
from app.db import get_db, utcnow
from app.models.application import ApplicationEvent
from app.models.job import Job, JobStatus, Priority
from app.schemas.job import JobRead, JobStatsResponse, JobStatusUpdate
from app.services.ai import BaseLLMProvider, get_llm_provider, safe_analyze_job, safe_generate_message
from app.services.matcher import normalized_from_job, score_job

router = APIRouter(prefix="/jobs", tags=["jobs"])


def get_llm_provider_dependency(settings: Settings = Depends(get_settings)) -> BaseLLMProvider | None:
    """Separate, overridable dependency (tests inject a fake provider here) so
    routes never have to construct a real Gemini/Anthropic client themselves."""
    try:
        return get_llm_provider(settings)
    except Exception:  # noqa: BLE001 — missing/invalid LLM config degrades to deterministic-only
        return None


def _get_job_or_404(db: Session, job_id: int) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return job


@router.get("", response_model=list[JobRead])
def list_jobs(
    min_score: int | None = None,
    source: str | None = None,
    status: JobStatus | None = None,
    db: Session = Depends(get_db),
) -> list[Job]:
    query = db.query(Job)
    if min_score is not None:
        query = query.filter(Job.match_score >= min_score)
    if source is not None:
        query = query.filter(Job.source == source)
    if status is not None:
        query = query.filter(Job.status == status)
    return query.order_by(Job.match_score.desc().nullslast(), Job.discovered_at.desc()).all()


@router.get("/high-priority", response_model=list[JobRead])
def high_priority_jobs(db: Session = Depends(get_db)) -> list[Job]:
    return (
        db.query(Job)
        .filter(Job.priority.in_([Priority.HIGH, Priority.STRONG]))
        .order_by(Job.match_score.desc())
        .all()
    )


@router.get("/stats", response_model=JobStatsResponse)
def job_stats(db: Session = Depends(get_db)) -> JobStatsResponse:
    total = db.query(func.count(Job.id)).scalar() or 0

    by_status = dict(db.query(Job.status, func.count(Job.id)).group_by(Job.status).all())
    by_priority = dict(
        db.query(Job.priority, func.count(Job.id)).filter(Job.priority.isnot(None)).group_by(Job.priority).all()
    )
    by_source = dict(db.query(Job.source, func.count(Job.id)).group_by(Job.source).all())
    avg_score = db.query(func.avg(Job.match_score)).filter(Job.match_score.isnot(None)).scalar()
    high_priority_count = (
        db.query(func.count(Job.id)).filter(Job.priority.in_([Priority.HIGH, Priority.STRONG])).scalar() or 0
    )

    return JobStatsResponse(
        total_jobs=total,
        by_status={k.value: v for k, v in by_status.items()},
        by_priority={k.value: v for k, v in by_priority.items()},
        by_source=by_source,
        average_match_score=round(avg_score, 1) if avg_score is not None else None,
        high_priority_count=high_priority_count,
    )


@router.get("/{job_id}", response_model=JobRead)
def get_job(job_id: int, db: Session = Depends(get_db)) -> Job:
    return _get_job_or_404(db, job_id)


@router.post("/{job_id}/analyze", response_model=JobRead)
def analyze_job(
    job_id: int,
    db: Session = Depends(get_db),
    provider: BaseLLMProvider | None = Depends(get_llm_provider_dependency),
) -> Job:
    job = _get_job_or_404(db, job_id)
    profile = get_profile()
    normalized = normalized_from_job(job)

    # Deterministic score is always recomputed — authoritative, per matcher.py.
    match_result = score_job(normalized, profile)
    job.match_score = match_result.match_score
    job.priority = match_result.priority
    job.match_reason = match_result.match_reason
    job.missing_skills = match_result.missing_skills

    # LLM adds qualitative narrative on top; failure here must not break the endpoint.
    analysis = safe_analyze_job(provider, normalized, profile) if provider else None
    if analysis is not None:
        job.red_flags = analysis.red_flags
        job.match_reason = f"{job.match_reason} | AI: {analysis.why_good_fit or analysis.why_not_good_fit}".strip(" |")
    else:
        job.notes = ((job.notes or "") + " [LLM analysis failed, manual review]").strip()

    db.commit()
    db.refresh(job)
    return job


@router.post("/{job_id}/generate-message", response_model=JobRead)
def generate_message(
    job_id: int,
    db: Session = Depends(get_db),
    provider: BaseLLMProvider | None = Depends(get_llm_provider_dependency),
) -> Job:
    job = _get_job_or_404(db, job_id)
    if job.match_score is None or job.match_score < 80:
        raise HTTPException(
            status_code=400,
            detail="Application messages are only generated for jobs scoring 80 or above.",
        )

    profile = get_profile()
    normalized = normalized_from_job(job)
    matched_skills = [s for s in normalized.required_skills if s.lower() in profile.all_skills_lower]

    message = safe_generate_message(provider, normalized, profile, matched_skills) if provider else None
    job.application_message = message or (
        f"Hi,\n\nI'm {profile.name}, a Python Backend Engineer with {profile.experience_years}+ years "
        f"of experience in {', '.join(profile.core_skills[:4])}. I'd be interested in discussing the "
        f"{job.job_title} role at {job.company}.\n\nThanks,\n{profile.name}"
    )
    db.commit()
    db.refresh(job)
    return job


@router.patch("/{job_id}/status", response_model=JobRead)
def update_job_status(job_id: int, update: JobStatusUpdate, db: Session = Depends(get_db)) -> Job:
    job = _get_job_or_404(db, job_id)
    old_status = job.status

    job.status = update.status
    if update.notes is not None:
        job.notes = update.notes
    if update.applied_at is not None:
        job.applied_at = update.applied_at
    if update.follow_up_date is not None:
        job.follow_up_date = update.follow_up_date

    db.add(
        ApplicationEvent(
            job_id=job.id,
            event_type="status_change",
            occurred_at=utcnow(),
            notes=f"{old_status.value} -> {update.status.value}",
        )
    )
    db.commit()
    db.refresh(job)
    return job
