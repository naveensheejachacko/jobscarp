"""Pydantic schemas shared across services and the API.

Kept separate from app/models/job.py (the ORM model) on purpose: these are the
shapes services pass between each other and the API returns, not the DB schema.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.job import JobStatus, Priority, WorkMode


class RawEmail(BaseModel):
    """What EmailFetcher hands to the extractor — one Gmail message, already fetched."""

    message_id: str
    source: str
    subject: str
    sender: str
    received_at: datetime | None = None
    snippet: str = ""
    html_body: str = ""
    text_body: str = ""


class ExtractedJob(BaseModel):
    """What a source parser produces from one RawEmail. Fields the parser couldn't
    find stay None rather than being guessed — the normalizer/matcher must be able
    to tell "unknown" apart from "zero"."""

    company: str
    job_title: str
    job_url: str | None = None
    source: str
    location: str | None = None
    salary_text: str | None = None       # raw, e.g. "12-18 LPA" — normalizer parses this
    experience_text: str | None = None   # raw, e.g. "2-4 years"
    job_description: str = ""
    recruiter_name: str | None = None
    recruiter_email: str | None = None
    received_at: datetime | None = None


class ExtractionFailure(BaseModel):
    """Returned instead of ExtractedJob when required fields can't be parsed.
    The email is stored for manual review, never silently dropped."""

    reason: str
    raw_subject: str
    raw_sender: str
    raw_snippet: str


class NormalizedJob(BaseModel):
    """ExtractedJob after normalization — canonical types the matcher/DB expect."""

    company: str
    job_title: str
    job_url: str | None = None
    source: str
    location: str | None = None
    work_mode: WorkMode = WorkMode.UNKNOWN
    salary_min: float | None = None
    salary_max: float | None = None
    currency: str = "INR"
    experience_min: float | None = None
    experience_max: float | None = None
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    job_description: str = ""
    recruiter_name: str | None = None
    recruiter_email: str | None = None
    received_at: datetime | None = None


class LLMJobAnalysis(BaseModel):
    """The strict JSON contract the LLM must return. Validated before use; a job
    is never trusted with unvalidated LLM output. match_score/priority here are
    advisory only — matcher.py's deterministic score is authoritative (see plan)."""

    model_config = ConfigDict(extra="ignore")

    match_score: int = Field(ge=0, le=100)
    priority: str
    technical_match: int = Field(ge=0, le=100)
    experience_match: int = Field(ge=0, le=100)
    salary_match: int = Field(ge=0, le=100)
    backend_relevance: int = Field(ge=0, le=100)
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    why_good_fit: str = ""
    why_not_good_fit: str = ""
    recommended_action: str = "REVIEW"


class MatchResult(BaseModel):
    """Output of the deterministic matcher — the authoritative score/priority."""

    match_score: int
    priority: Priority
    match_reason: str
    missing_skills: list[str] = Field(default_factory=list)
    sub_scores: dict[str, float] = Field(default_factory=dict)


class JobStatusUpdate(BaseModel):
    status: JobStatus
    notes: str | None = None
    applied_at: datetime | None = None
    follow_up_date: datetime | None = None


class JobRead(BaseModel):
    """API response shape for a Job row — a direct mirror of the ORM model,
    kept separate so the DB schema can evolve without automatically reshaping
    every API response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    company: str
    job_title: str
    job_url: str | None
    source: str
    location: str | None
    work_mode: WorkMode
    salary_min: float | None
    salary_max: float | None
    currency: str
    experience_min: float | None
    experience_max: float | None
    required_skills: list[str]
    preferred_skills: list[str]
    job_description: str | None
    received_at: datetime | None
    discovered_at: datetime
    match_score: int | None
    priority: Priority | None
    match_reason: str | None
    missing_skills: list[str]
    red_flags: list[str]
    application_message: str | None
    status: JobStatus
    recruiter_name: str | None
    recruiter_email: str | None
    applied_at: datetime | None
    follow_up_date: datetime | None
    notes: str | None


class JobStatsResponse(BaseModel):
    total_jobs: int
    by_status: dict[str, int]
    by_priority: dict[str, int]
    by_source: dict[str, int]
    average_match_score: float | None
    high_priority_count: int
