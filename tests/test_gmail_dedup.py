"""Stage 4: Gmail API is mocked entirely — these tests only prove the dedup
contract (never process the same message id twice) and the raw-message parser."""
import base64

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models.application import ProcessedMessage
from app.services.gmail import EmailFetcher, parse_raw_message


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


class FakeGmailClient:
    """Stands in for GmailClient — no real network/API calls."""

    def __init__(self, message_ids: list[str], messages_by_id: dict[str, dict]):
        self._message_ids = message_ids
        self._messages_by_id = messages_by_id
        self.get_message_calls: list[str] = []

    def list_message_ids(self, query: str, max_results: int = 100) -> list[str]:
        return list(self._message_ids)

    def get_message(self, message_id: str) -> dict:
        self.get_message_calls.append(message_id)
        return self._messages_by_id[message_id]


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


def test_fetch_new_skips_already_processed_messages(session_factory):
    with session_factory() as session:
        session.add(ProcessedMessage(gmail_message_id="already-seen", source="cutshort"))
        session.commit()

    fake_client = FakeGmailClient(
        message_ids=["already-seen", "new-msg"],
        messages_by_id={"new-msg": _fake_message("new-msg", "New Job", "alerts@cutshort.io", "Body text")},
    )
    fetcher = EmailFetcher(fake_client, session_factory)

    emails = fetcher.fetch_new("from:(cutshort.io)", "cutshort")

    assert len(emails) == 1
    assert emails[0].message_id == "new-msg"
    assert fake_client.get_message_calls == ["new-msg"]  # never fetched the already-seen one


def test_mark_processed_then_refetch_returns_nothing(session_factory):
    fake_client = FakeGmailClient(
        message_ids=["msg-1"],
        messages_by_id={"msg-1": _fake_message("msg-1", "Job Alert", "alerts@instahyre.com", "Body")},
    )
    fetcher = EmailFetcher(fake_client, session_factory)

    first_run = fetcher.fetch_new("from:(instahyre.com)", "instahyre")
    assert len(first_run) == 1
    fetcher.mark_processed("msg-1", "instahyre", job_id=None, extraction_failed=False)

    second_run = fetcher.fetch_new("from:(instahyre.com)", "instahyre")
    assert second_run == []


def test_mark_processed_is_idempotent(session_factory):
    fetcher = EmailFetcher(FakeGmailClient([], {}), session_factory)
    fetcher.mark_processed("msg-x", "cutshort")
    fetcher.mark_processed("msg-x", "cutshort")  # must not raise / double-insert

    with session_factory() as session:
        assert session.query(ProcessedMessage).filter_by(gmail_message_id="msg-x").count() == 1


def test_mark_processed_records_extraction_failure_for_manual_review(session_factory):
    fetcher = EmailFetcher(FakeGmailClient([], {}), session_factory)
    fetcher.mark_processed(
        "msg-fail",
        "cutshort",
        extraction_failed=True,
        raw_subject="Weird Job Alert",
        raw_sender="alerts@cutshort.io",
        raw_snippet="couldn't parse this one",
    )

    with session_factory() as session:
        row = session.get(ProcessedMessage, "msg-fail")
        assert row.extraction_failed is True
        assert row.raw_subject == "Weird Job Alert"


def test_parse_raw_message_extracts_headers_and_body():
    message = _fake_message("m1", "Backend Engineer at Acme", "jobs@cutshort.io", "Great backend role")
    raw = parse_raw_message(message, "cutshort")

    assert raw.message_id == "m1"
    assert raw.subject == "Backend Engineer at Acme"
    assert raw.sender == "jobs@cutshort.io"
    assert "Great backend role" in raw.text_body
    assert raw.received_at is not None
