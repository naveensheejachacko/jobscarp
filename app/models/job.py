"""The Job model — the core record the whole pipeline builds up and the API/Sheets read."""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, utcnow


class JobStatus(str, enum.Enum):
    NEW = "NEW"
    SHORTLISTED = "SHORTLISTED"
    APPLY = "APPLY"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"
    INTERVIEW = "INTERVIEW"
    OFFER = "OFFER"
    SKIPPED = "SKIPPED"
    DUPLICATE = "DUPLICATE"


class WorkMode(str, enum.Enum):
    ONSITE = "ONSITE"
    HYBRID = "HYBRID"
    REMOTE = "REMOTE"
    UNKNOWN = "UNKNOWN"


class Priority(str, enum.Enum):
    HIGH = "HIGH"           # 🔥 90-100
    STRONG = "STRONG"       # 🟢 80-89
    CONSIDER = "CONSIDER"   # 🟡 70-79
    LOW = "LOW"             # ⚪ 60-69
    SKIP = "SKIP"           # 🔴 <60


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    company: Mapped[str] = mapped_column(String(255), nullable=False)
    job_title: Mapped[str] = mapped_column(String(255), nullable=False)
    job_url: Mapped[str | None] = mapped_column(String(1024), nullable=True, unique=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)  # "cutshort" | "instahyre" | ...

    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    work_mode: Mapped[WorkMode] = mapped_column(
        Enum(WorkMode, native_enum=False), default=WorkMode.UNKNOWN, nullable=False
    )

    salary_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(10), default="INR", nullable=False)

    experience_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    experience_max: Mapped[float | None] = mapped_column(Float, nullable=True)

    required_skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    preferred_skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    job_description: Mapped[str | None] = mapped_column(Text, nullable=True)

    received_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    match_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    priority: Mapped[Priority | None] = mapped_column(Enum(Priority, native_enum=False), nullable=True)
    match_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    missing_skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    red_flags: Mapped[list[str]] = mapped_column(JSON, default=list)

    application_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False), default=JobStatus.NEW, nullable=False
    )

    recruiter_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recruiter_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    follow_up_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
