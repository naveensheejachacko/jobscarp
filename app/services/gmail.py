"""Gmail ingestion via the official Gmail API — no scraping, no scripted logins.

GmailClient owns OAuth + the raw API calls (list/get messages, send mail).
EmailFetcher owns the "never process the same email twice" contract by checking
and recording message ids against the ProcessedMessage table before extraction
is attempted anywhere downstream.
"""
from __future__ import annotations

import base64
import logging
from collections.abc import Callable
from email.utils import parsedate_to_datetime

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.models.application import ProcessedMessage
from app.schemas.job import RawEmail

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


def _is_retryable_http_error(exc: BaseException) -> bool:
    return isinstance(exc, HttpError) and (exc.resp.status == 429 or exc.resp.status >= 500)


class GmailClient:
    """Thin wrapper around the Gmail API. All network calls go through here."""

    def __init__(self, credentials_path: str, token_path: str):
        self.credentials_path = credentials_path
        self.token_path = token_path
        self._service = None

    def _load_credentials(self) -> Credentials:
        creds: Credentials | None = None
        try:
            creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)
        except FileNotFoundError:
            creds = None

        if creds and creds.valid:
            return creds

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(self.credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(self.token_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
        return creds

    def get_service(self):
        if self._service is None:
            creds = self._load_credentials()
            self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return self._service

    @retry(
        retry=retry_if_exception(_is_retryable_http_error),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        reraise=True,
    )
    def list_message_ids(self, query: str, max_results: int = 100) -> list[str]:
        service = self.get_service()
        ids: list[str] = []
        page_token = None
        while True:
            resp = (
                service.users()
                .messages()
                .list(userId="me", q=query, maxResults=max_results, pageToken=page_token)
                .execute()
            )
            ids.extend(m["id"] for m in resp.get("messages", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return ids

    @retry(
        retry=retry_if_exception(_is_retryable_http_error),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        reraise=True,
    )
    def get_message(self, message_id: str) -> dict:
        service = self.get_service()
        return service.users().messages().get(userId="me", id=message_id, format="full").execute()

    def send_email(self, to: str, subject: str, body_text: str) -> None:
        import email.mime.text

        message = email.mime.text.MIMEText(body_text)
        message["to"] = to
        message["subject"] = subject
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        service = self.get_service()
        service.users().messages().send(userId="me", body={"raw": raw}).execute()


def _header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _decode_part_body(data: str) -> str:
    return base64.urlsafe_b64decode(data.encode("utf-8") + b"===").decode("utf-8", errors="replace")


def _walk_bodies(payload: dict) -> tuple[str, str]:
    """Returns (text_body, html_body), recursing into multipart messages."""
    text_body, html_body = "", ""
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data")

    if body_data and mime_type == "text/plain":
        text_body += _decode_part_body(body_data)
    elif body_data and mime_type == "text/html":
        html_body += _decode_part_body(body_data)

    for part in payload.get("parts", []) or []:
        t, h = _walk_bodies(part)
        text_body += t
        html_body += h

    return text_body, html_body


def parse_raw_message(message: dict, source: str) -> RawEmail:
    payload = message.get("payload", {})
    headers = payload.get("headers", [])
    text_body, html_body = _walk_bodies(payload)

    received_at = None
    date_header = _header(headers, "Date")
    if date_header:
        try:
            received_at = parsedate_to_datetime(date_header)
        except (ValueError, TypeError):
            received_at = None

    return RawEmail(
        message_id=message["id"],
        source=source,
        subject=_header(headers, "Subject"),
        sender=_header(headers, "From"),
        received_at=received_at,
        snippet=message.get("snippet", ""),
        html_body=html_body,
        text_body=text_body,
    )


class EmailFetcher:
    """Fetches only messages not already recorded in ProcessedMessage.

    Callers are responsible for calling `mark_processed` once a message has been
    handled (successfully extracted, or failed) — the ledger row is what prevents
    the same email from being processed again on the next scheduler tick.
    """

    def __init__(self, gmail_client: GmailClient, session_factory: Callable[[], Session]):
        self.gmail_client = gmail_client
        self.session_factory = session_factory

    def fetch_new(self, query: str, source: str) -> list[RawEmail]:
        message_ids = self.gmail_client.list_message_ids(query)
        unseen_ids = self._filter_unprocessed(message_ids)

        emails: list[RawEmail] = []
        for message_id in unseen_ids:
            try:
                raw_message = self.gmail_client.get_message(message_id)
            except HttpError as exc:
                logger.warning("gmail.fetch_failed", extra={"message_id": message_id, "error": str(exc)})
                continue
            emails.append(parse_raw_message(raw_message, source))
        return emails

    def _filter_unprocessed(self, message_ids: list[str]) -> list[str]:
        if not message_ids:
            return []
        with self._session() as session:
            already_seen = {
                row.gmail_message_id
                for row in session.query(ProcessedMessage.gmail_message_id)
                .filter(ProcessedMessage.gmail_message_id.in_(message_ids))
                .all()
            }
        return [mid for mid in message_ids if mid not in already_seen]

    def mark_processed(
        self,
        message_id: str,
        source: str,
        *,
        job_id: int | None = None,
        extraction_failed: bool = False,
        raw_subject: str | None = None,
        raw_sender: str | None = None,
        raw_snippet: str | None = None,
    ) -> None:
        with self._session() as session:
            existing = session.get(ProcessedMessage, message_id)
            if existing is not None:
                return  # already recorded — never double-insert
            session.add(
                ProcessedMessage(
                    gmail_message_id=message_id,
                    source=source,
                    job_id=job_id,
                    extraction_failed=extraction_failed,
                    raw_subject=raw_subject,
                    raw_sender=raw_sender,
                    raw_snippet=raw_snippet,
                )
            )
            session.commit()

    def _session(self) -> Session:
        return self.session_factory()
