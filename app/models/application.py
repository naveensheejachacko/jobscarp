"""Supporting tables: Gmail-message dedup ledger and the application-event audit trail."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, utcnow


class ProcessedMessage(Base):
    """Every Gmail message id we've ever looked at, so nothing is processed twice.

    job_id is NULL when extraction failed — the row still exists (so the email is
    never retried) and raw_subject/raw_sender/raw_snippet are kept for manual review.
    """

    __tablename__ = "processed_messages"

    gmail_message_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)

    extraction_failed: Mapped[bool] = mapped_column(default=False, nullable=False)
    raw_subject: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    raw_sender: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)


class ApplicationEvent(Base):
    """Lightweight audit trail of what happened on a job (status changes, notes, etc.)."""

    __tablename__ = "application_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. "status_change"
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
