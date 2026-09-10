"""Prevents the same job (re-sent through multiple alert emails, or re-alerted
later) from ever becoming two rows.

Checked in order, cheapest/most-precise first:
  1. exact job_url match
  2. normalized company+title match
  3. normalized company+title+location match
  4. fuzzy title+company match (rapidfuzz) above a configurable threshold

The first strategy that finds an existing Job wins; the caller should update
that row instead of inserting a new one.
"""
from __future__ import annotations

import re

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from app.models.job import Job
from app.schemas.job import NormalizedJob

DEFAULT_FUZZY_THRESHOLD = 90.0


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def find_duplicate(
    session: Session,
    job: NormalizedJob,
    *,
    fuzzy_threshold: float = DEFAULT_FUZZY_THRESHOLD,
) -> Job | None:
    # 1. exact job_url
    if job.job_url:
        existing = session.query(Job).filter(Job.job_url == job.job_url).one_or_none()
        if existing is not None:
            return existing

    candidates = session.query(Job).all()
    if not candidates:
        return None

    target_company_title = _slug(job.company) + "|" + _slug(job.job_title)
    target_full = target_company_title + "|" + _slug(job.location or "")

    # 2. normalized company+title
    for candidate in candidates:
        if _slug(candidate.company) + "|" + _slug(candidate.job_title) == target_company_title:
            return candidate

    # 3. normalized company+title+location
    for candidate in candidates:
        candidate_full = (
            _slug(candidate.company) + "|" + _slug(candidate.job_title) + "|" + _slug(candidate.location or "")
        )
        if candidate_full == target_full:
            return candidate

    # 4. fuzzy match on "title company" as one string
    target_text = f"{job.job_title} {job.company}"
    best_candidate: Job | None = None
    best_score = 0.0
    for candidate in candidates:
        candidate_text = f"{candidate.job_title} {candidate.company}"
        score = fuzz.token_sort_ratio(target_text, candidate_text)
        if score > best_score:
            best_score = score
            best_candidate = candidate

    if best_candidate is not None and best_score >= fuzzy_threshold:
        return best_candidate

    return None
