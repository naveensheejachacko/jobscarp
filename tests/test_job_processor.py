"""Stage 12: the orchestrator, exercised with fake Gmail/LLM/notifier — no
real network calls. Confirms one email -> one scored Job, extraction
failures are recorded (not silently dropped), and the scheduler registers
without executing immediately."""
import base64

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models.application import ProcessedMessage
from app.models.job import Job
from app.workers.job_processor import run_once, start_scheduler, stop_scheduler


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("utf-8").rstrip("=")


def _fake_message(message_id: str, subject: str, sender: str, body: str) -> dict:
    return {
        "id": message_id,
        "snippet": body[:50],
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
                {"name": "Date", "value": "Tue, 02 Sep 2026 10:00:00 +0000"},
            ],
            "body": {"data": _b64(body)},
        },
    }


class FakeGmailClient:
    def __init__(self, ids_by_query: dict[str, list[str]], messages_by_id: dict[str, dict]):
        self._ids_by_query = ids_by_query
        self._messages_by_id = messages_by_id
        self.sent_emails: list[tuple[str, str, str]] = []

    def list_message_ids(self, query: str, max_results: int = 100) -> list[str]:
        return list(self._ids_by_query.get(query, []))

    def get_message(self, message_id: str) -> dict:
        return self._messages_by_id[message_id]

    def send_email(self, to: str, subject: str, body_text: str) -> None:
        self.sent_emails.append((to, subject, body_text))

    def _load_credentials(self):
        return object()


class FakeNotifier:
    def __init__(self):
        self.notified: list[Job] = []

    def notify(self, job: Job) -> None:
        self.notified.append(job)


def test_run_once_processes_a_new_email_into_a_scored_job(session_factory, monkeypatch):
    monkeypatch.setenv("GMAIL_QUERY_CUTSHORT", "from:(cutshort.io)")
    monkeypatch.setenv("GMAIL_QUERY_INSTAHYRE", "from:(instahyre.com)")
    from app.config import get_settings
    get_settings.cache_clear()

    body = "Location: Bangalore\nExperience: 2-5 years\nSalary: 20-30 LPA\nPython, FastAPI, PostgreSQL"
    message = _fake_message("m1", "Backend Engineer at Acme", "alerts@cutshort.io", body)
    gmail_client = FakeGmailClient(
        ids_by_query={"from:(cutshort.io)": ["m1"], "from:(instahyre.com)": []},
        messages_by_id={"m1": message},
    )
    notifier = FakeNotifier()

    jobs = run_once(
        session_factory=session_factory,
        gmail_client=gmail_client,
        llm_provider=None,
        notifier=notifier,
        sheets_sync=False,
    )

    assert len(jobs) == 1
    assert jobs[0].company == "Acme"
    assert jobs[0].match_score is not None and jobs[0].match_score >= 80

    with session_factory() as session:
        assert session.query(Job).count() == 1
        assert session.get(ProcessedMessage, "m1") is not None

    get_settings.cache_clear()


def test_run_once_never_reprocesses_same_message_on_second_call(session_factory, monkeypatch):
    monkeypatch.setenv("GMAIL_QUERY_CUTSHORT", "from:(cutshort.io)")
    monkeypatch.setenv("GMAIL_QUERY_INSTAHYRE", "from:(instahyre.com)")
    from app.config import get_settings
    get_settings.cache_clear()

    body = "Location: Remote\nExperience: 2-4 years\nSalary: 14-18 LPA\nPython, Django"
    message = _fake_message("m2", "Backend Developer - Zenith", "alerts@instahyre.com", body)
    gmail_client = FakeGmailClient(
        ids_by_query={"from:(cutshort.io)": [], "from:(instahyre.com)": ["m2"]},
        messages_by_id={"m2": message},
    )

    first = run_once(session_factory=session_factory, gmail_client=gmail_client, llm_provider=None, sheets_sync=False, notifier=FakeNotifier())
    second = run_once(session_factory=session_factory, gmail_client=gmail_client, llm_provider=None, sheets_sync=False, notifier=FakeNotifier())

    assert len(first) == 1
    assert len(second) == 0  # already processed — never a duplicate row

    with session_factory() as session:
        assert session.query(Job).count() == 1

    get_settings.cache_clear()


def test_run_once_records_extraction_failure_without_crashing(session_factory, monkeypatch):
    monkeypatch.setenv("GMAIL_QUERY_CUTSHORT", "from:(cutshort.io)")
    monkeypatch.setenv("GMAIL_QUERY_INSTAHYRE", "from:(instahyre.com)")
    from app.config import get_settings
    get_settings.cache_clear()

    message = _fake_message("m3", "Weekly digest — top stories", "alerts@cutshort.io", "no job structure here")
    gmail_client = FakeGmailClient(
        ids_by_query={"from:(cutshort.io)": ["m3"], "from:(instahyre.com)": []},
        messages_by_id={"m3": message},
    )

    jobs = run_once(session_factory=session_factory, gmail_client=gmail_client, llm_provider=None, sheets_sync=False, notifier=FakeNotifier())

    assert jobs == []
    with session_factory() as session:
        assert session.query(Job).count() == 0
        row = session.get(ProcessedMessage, "m3")
        assert row is not None
        assert row.extraction_failed is True

    get_settings.cache_clear()


def test_run_once_sends_notification_for_high_score_job(session_factory, monkeypatch):
    monkeypatch.setenv("GMAIL_QUERY_CUTSHORT", "from:(cutshort.io)")
    monkeypatch.setenv("GMAIL_QUERY_INSTAHYRE", "from:(instahyre.com)")
    monkeypatch.setenv("NOTIFY_MIN_SCORE", "80")
    from app.config import get_settings
    get_settings.cache_clear()

    body = "Location: Bangalore\nExperience: 2-5 years\nSalary: 20-30 LPA\nPython, FastAPI, PostgreSQL"
    message = _fake_message("m4", "Senior Backend Engineer at Acme", "alerts@cutshort.io", body)
    gmail_client = FakeGmailClient(
        ids_by_query={"from:(cutshort.io)": ["m4"], "from:(instahyre.com)": []},
        messages_by_id={"m4": message},
    )
    notifier = FakeNotifier()

    run_once(session_factory=session_factory, gmail_client=gmail_client, llm_provider=None, notifier=notifier, sheets_sync=False)

    assert len(notifier.notified) == 1
    get_settings.cache_clear()


def test_scheduler_starts_and_stops_without_firing_immediately():
    scheduler = start_scheduler()
    try:
        job = scheduler.get_job("job_pipeline")
        assert job is not None
        assert job.next_run_time is None  # registered, but not fired on startup
    finally:
        stop_scheduler(scheduler)
