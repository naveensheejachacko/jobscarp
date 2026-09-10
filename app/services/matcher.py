"""Deterministic, weighted job-match scoring.

This is the authoritative score (0-100) stored on every Job — not the LLM's.
Keeping it rule-based makes it testable, explainable, and immune to an LLM
inventing or forgetting candidate facts. The LLM (services/ai.py) only adds
qualitative narrative on top of this score; see the plan's "score authority
split" section for why.

Weights (must sum to 100):
    technical skill match   30
    experience match        15
    role/title match        10
    salary match             15
    backend relevance       10
    cloud/infra match         5
    database match            5
    company/role quality      5
    location/work mode        5
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.config import CandidateProfile
from app.models.job import Job, Priority, WorkMode
from app.schemas.job import MatchResult, NormalizedJob

WEIGHTS = {
    "technical_skill": 30,
    "experience": 15,
    "title": 10,
    "salary": 15,
    "backend_relevance": 10,
    "cloud_infra": 5,
    "database": 5,
    "company_quality": 5,
    "location_work_mode": 5,
}
assert sum(WEIGHTS.values()) == 100

CLOUD_INFRA_SKILLS = {"aws", "ecs", "fargate", "ecr", "ec2", "s3", "iam", "lambda", "gcp", "azure"}
DATABASE_SKILLS = {"postgresql", "postgres", "mysql", "mongodb", "redis", "elasticsearch", "sql", "nosql"}

FRONTEND_PRIMARY_TITLE_HINTS = {"frontend", "front-end", "front end", "ui developer", "react developer"}
OTHER_BACKEND_STACK_HINTS = {"java developer", ".net developer", "node.js developer", "node developer"}

# Simple, editable keyword signal for company/role quality — not a hard filter, just a nudge.
LOW_QUALITY_JD_HINTS = {"bench", "staffing", "any graduate", "walk-in", "bulk hiring"}
PRODUCT_JD_HINTS = {"product", "platform", "our users", "our customers", "engineering team"}


@dataclass
class ScoreBreakdown:
    sub_scores: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> float:
        return sum(
            (self.sub_scores.get(key, 0.0) / 100.0) * weight for key, weight in WEIGHTS.items()
        )


def _score_technical_skills(job: NormalizedJob, profile: CandidateProfile) -> tuple[float, list[str]]:
    job_skills_lower = {s.lower() for s in [*job.required_skills, *job.preferred_skills]}
    core_lower = {s.lower() for s in profile.core_skills}
    bonus_lower = {s.lower() for s in profile.bonus_skills}

    if not job_skills_lower:
        return 50.0, []  # JD didn't state skills explicitly — neutral, not punished

    matched_core = job_skills_lower & core_lower
    matched_bonus = job_skills_lower & bonus_lower
    missing = sorted(job_skills_lower - core_lower - bonus_lower)

    # Core skills weighted ~3x a bonus skill. Score is coverage of the JD's stated
    # skills that the candidate actually has, core-weighted.
    core_in_jd = job_skills_lower & (core_lower | bonus_lower)  # skills we can even evaluate
    if not core_in_jd:
        # None of the JD's stated skills are in the candidate's vocabulary at all.
        return 20.0, missing

    weighted_have = len(matched_core) * 3 + len(matched_bonus) * 1
    weighted_total = len(job_skills_lower) * 3  # upper bound if every stated skill were core
    score = (weighted_have / weighted_total) * 100.0
    return round(min(score, 100.0), 1), missing


def _score_experience(job: NormalizedJob, profile: CandidateProfile) -> tuple[float, str | None]:
    candidate_years = profile.experience_years
    exp_min, exp_max = job.experience_min, job.experience_max

    if exp_min is None and exp_max is None:
        return 60.0, None  # unstated — mild neutral score, not a penalty

    lo = exp_min if exp_min is not None else 0.0
    hi = exp_max if exp_max is not None else lo + 3.0  # "6+" style — assume a soft ceiling for scoring only

    if lo <= candidate_years <= hi:
        return 100.0, None

    if candidate_years < lo:
        gap = lo - candidate_years
        score = max(0.0, 100.0 - gap * 25.0)
        hard_ceiling = profile.avoid.min_experience_hard_ceiling_years
        note = None
        if lo >= hard_ceiling:
            score = min(score, 25.0)
            note = f"JD requires {lo:g}+ years vs your {candidate_years:g} — well above your experience range"
        elif gap > 1:
            note = f"JD requires {lo:g}-{hi:g} years vs your {candidate_years:g}"
        return score, note

    # candidate has MORE experience than the JD's max — usually still fine, small taper only if huge gap
    gap = candidate_years - hi
    score = max(60.0, 100.0 - gap * 10.0)
    return score, None


def _score_title(job: NormalizedJob, profile: CandidateProfile) -> float:
    title_lower = job.job_title.lower()
    for target in profile.target_titles:
        target_lower = target.lower()
        if target_lower in title_lower or title_lower in target_lower:
            return 100.0
    # partial credit: shares "backend"/"engineer"/"developer" with a target title
    generic_terms = {"backend", "engineer", "developer", "software"}
    if any(term in title_lower for term in generic_terms):
        return 60.0
    return 30.0


def _score_salary(job: NormalizedJob, profile: CandidateProfile) -> tuple[float, str | None]:
    if job.salary_min is None and job.salary_max is None:
        return 60.0, None  # unstated — neutral

    job_max = job.salary_max if job.salary_max is not None else job.salary_min
    job_min = job.salary_min if job.salary_min is not None else job.salary_max

    floor = profile.salary.min_acceptable_lpa
    preferred = profile.salary.preferred_lpa

    if job_max is not None and job_max >= preferred:
        return 100.0, None
    if job_max is not None and job_max >= floor:
        # scales between 70 (right at floor) and 100 (at preferred)
        span = max(preferred - floor, 0.01)
        score = 70.0 + (job_max - floor) / span * 30.0
        return round(min(score, 100.0), 1), None
    if job_min is not None and job_min < floor:
        gap = floor - job_min
        score = max(0.0, 60.0 - gap * 6.0)
        note = f"Salary range (up to ₹{job_max or job_min:g} LPA) is below your ₹{floor:g}+ LPA floor"
        return score, note

    return 50.0, None


def _score_backend_relevance(job: NormalizedJob, profile: CandidateProfile) -> tuple[float, list[str]]:
    title_lower = job.job_title.lower()
    desc_lower = job.job_description.lower()
    red_flags: list[str] = []
    score = 100.0

    if any(hint in title_lower for hint in FRONTEND_PRIMARY_TITLE_HINTS):
        score -= 60.0
        red_flags.append("Title suggests a frontend-primary role")

    if "full stack" in title_lower or "fullstack" in title_lower or "full-stack" in title_lower:
        score -= 20.0
        red_flags.append("Full-stack role — backend is only part of the job")

    if "react" in desc_lower and "python" not in desc_lower and "django" not in desc_lower and "fastapi" not in desc_lower:
        score -= 30.0
        red_flags.append("JD emphasizes React with no backend Python framework mentioned")
    elif "react" in title_lower or ("react" in desc_lower and desc_lower.count("react") > 3):
        score -= 15.0
        red_flags.append("React appears prominently — verify backend is the primary focus")

    for stack in profile.avoid.other_backend_stacks:
        if _stack_in_text(title_lower, stack):
            score -= 50.0
            red_flags.append(f"Title suggests a {stack} role, not Python backend")

    return max(0.0, score), red_flags


def _stack_in_text(haystack: str, stack: str) -> bool:
    token = stack.lower().strip()
    if not token:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", haystack) is not None


def avoided_stack(job: NormalizedJob, profile: CandidateProfile) -> str | None:
    """Java / .NET / JavaScript etc. — title or required skills, not a vague JD mention."""
    title = job.job_title.lower()
    skill_blob = " ".join(job.required_skills).lower()
    for stack in profile.avoid.other_backend_stacks:
        if _stack_in_text(title, stack) or _stack_in_text(skill_blob, stack):
            return stack
    return None


def _score_cloud_infra(job: NormalizedJob) -> float:
    job_skills_lower = {s.lower() for s in job.required_skills}
    hits = job_skills_lower & CLOUD_INFRA_SKILLS
    if not hits:
        return 40.0  # not mentioned isn't disqualifying, just no bonus
    return min(100.0, 60.0 + len(hits) * 15.0)


def _score_database(job: NormalizedJob) -> float:
    job_skills_lower = {s.lower() for s in job.required_skills}
    hits = job_skills_lower & DATABASE_SKILLS
    if not hits:
        return 40.0
    return min(100.0, 60.0 + len(hits) * 20.0)


def _score_company_quality(job: NormalizedJob) -> float:
    desc_lower = job.job_description.lower()
    score = 70.0  # neutral baseline
    if any(hint in desc_lower for hint in LOW_QUALITY_JD_HINTS):
        score -= 30.0
    if any(hint in desc_lower for hint in PRODUCT_JD_HINTS):
        score += 20.0
    return max(0.0, min(100.0, score))


def _score_location_work_mode(job: NormalizedJob, profile: CandidateProfile) -> float:
    if job.work_mode == WorkMode.REMOTE:
        return 100.0
    location_lower = (job.location or "").lower()
    for preferred in profile.locations:
        if preferred.lower() in location_lower:
            return 90.0 if job.work_mode == WorkMode.HYBRID else 80.0
    if job.work_mode == WorkMode.UNKNOWN:
        return 50.0
    return 30.0  # onsite, non-preferred location


def normalized_from_job(job: Job) -> NormalizedJob:
    """Reconstructs a NormalizedJob from a stored Job row, so services that only
    know NormalizedJob (matcher, ai) can be re-run against already-persisted jobs
    (e.g. from the POST /jobs/{id}/analyze endpoint)."""
    return NormalizedJob(
        company=job.company,
        job_title=job.job_title,
        job_url=job.job_url,
        source=job.source,
        location=job.location,
        work_mode=job.work_mode,
        salary_min=job.salary_min,
        salary_max=job.salary_max,
        currency=job.currency,
        experience_min=job.experience_min,
        experience_max=job.experience_max,
        required_skills=job.required_skills or [],
        preferred_skills=job.preferred_skills or [],
        job_description=job.job_description or "",
        recruiter_name=job.recruiter_name,
        recruiter_email=job.recruiter_email,
        received_at=job.received_at,
    )


def priority_for_score(score: int) -> Priority:
    if score >= 90:
        return Priority.HIGH
    if score >= 80:
        return Priority.STRONG
    if score >= 70:
        return Priority.CONSIDER
    if score >= 60:
        return Priority.LOW
    return Priority.SKIP


def score_job(job: NormalizedJob, profile: CandidateProfile) -> MatchResult:
    breakdown = ScoreBreakdown()

    tech_score, missing_skills = _score_technical_skills(job, profile)
    breakdown.sub_scores["technical_skill"] = tech_score

    exp_score, exp_note = _score_experience(job, profile)
    breakdown.sub_scores["experience"] = exp_score
    if exp_note:
        breakdown.notes.append(exp_note)

    breakdown.sub_scores["title"] = _score_title(job, profile)

    salary_score, salary_note = _score_salary(job, profile)
    breakdown.sub_scores["salary"] = salary_score
    if salary_note:
        breakdown.notes.append(salary_note)

    backend_score, red_flags = _score_backend_relevance(job, profile)
    breakdown.sub_scores["backend_relevance"] = backend_score
    breakdown.notes.extend(red_flags)

    breakdown.sub_scores["cloud_infra"] = _score_cloud_infra(job)
    breakdown.sub_scores["database"] = _score_database(job)
    breakdown.sub_scores["company_quality"] = _score_company_quality(job)
    breakdown.sub_scores["location_work_mode"] = _score_location_work_mode(job, profile)

    final_score = round(breakdown.total)
    final_score = max(0, min(100, final_score))
    stack_hit = avoided_stack(job, profile)
    if stack_hit:
        final_score = min(final_score, 40)
        breakdown.notes.insert(0, f"Avoided stack ({stack_hit}) — not Python backend")
    priority = priority_for_score(final_score)

    matched_core = sorted({s for s in job.required_skills if s.lower() in profile.all_skills_lower})
    reason_parts = []
    if matched_core:
        reason_parts.append(f"Matches: {', '.join(matched_core)}")
    if missing_skills:
        reason_parts.append(f"Missing: {', '.join(missing_skills)}")
    reason_parts.extend(breakdown.notes)
    match_reason = " | ".join(reason_parts) if reason_parts else "Scored on structured fields only."

    return MatchResult(
        match_score=final_score,
        priority=priority,
        match_reason=match_reason,
        missing_skills=missing_skills,
        sub_scores=breakdown.sub_scores,
    )
