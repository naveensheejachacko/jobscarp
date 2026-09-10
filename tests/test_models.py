"""Stage 3: verify the ORM models create clean tables and round-trip a row.

Uses an in-memory SQLite engine via Base.metadata.create_all — this proves the
model definitions themselves are consistent (columns, FKs, enums) independent
of the Alembic migration, which is exercised separately in test_migrations.py.
"""
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import ApplicationEvent, Job, JobStatus, Priority, ProcessedMessage, WorkMode


def _sqlite_engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def test_all_tables_create():
    engine = _sqlite_engine()
    table_names = set(Base.metadata.tables.keys())
    assert table_names == {"jobs", "application_events", "processed_messages"}


def test_job_round_trip():
    engine = _sqlite_engine()
    with Session(engine) as session:
        job = Job(
            company="Acme",
            job_title="Backend Engineer",
            source="cutshort",
            work_mode=WorkMode.REMOTE,
            required_skills=["Python", "Django"],
            status=JobStatus.NEW,
        )
        session.add(job)
        session.commit()

        fetched = session.query(Job).one()
        assert fetched.company == "Acme"
        assert fetched.status == JobStatus.NEW
        assert fetched.required_skills == ["Python", "Django"]
        assert fetched.work_mode == WorkMode.REMOTE


def test_processed_message_dedup_key_is_message_id():
    engine = _sqlite_engine()
    with Session(engine) as session:
        session.add(ProcessedMessage(gmail_message_id="msg-1", source="cutshort"))
        session.commit()
        assert session.get(ProcessedMessage, "msg-1") is not None


def test_application_event_links_to_job():
    engine = _sqlite_engine()
    with Session(engine) as session:
        job = Job(company="Acme", job_title="Backend Engineer", source="instahyre")
        session.add(job)
        session.commit()

        event = ApplicationEvent(job_id=job.id, event_type="status_change", notes="NEW -> SHORTLISTED")
        session.add(event)
        session.commit()

        assert session.query(ApplicationEvent).filter_by(job_id=job.id).count() == 1


def test_priority_enum_values_match_spec_thresholds():
    assert {p.value for p in Priority} == {"HIGH", "STRONG", "CONSIDER", "LOW", "SKIP"}


def test_job_status_enum_matches_spec():
    assert {s.value for s in JobStatus} == {
        "NEW", "SHORTLISTED", "APPLY", "APPLIED", "REJECTED",
        "INTERVIEW", "OFFER", "SKIPPED", "DUPLICATE",
    }
