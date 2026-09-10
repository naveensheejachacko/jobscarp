"""Per-source job extraction from raw alert emails.

Each source gets its own parser implementing BaseExtractor. Adding a new source
(LinkedIn, Naukri, ...) later means writing one new subclass here plus a
GMAIL_QUERY_* entry in .env — nothing else in the pipeline changes.

Parsers never guess: if a required field can't be found, they return an
ExtractionFailure so the email is queued for manual review instead of a job
record with fabricated data.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod

from bs4 import BeautifulSoup

from app.schemas.job import ExtractedJob, ExtractionFailure, RawEmail

REQUIRED_FIELDS_MISSING = "Could not confidently extract company/title from this email"

_URL_RE = re.compile(r"https?://[^\s\"'<>)]+")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _best_text(raw: RawEmail) -> str:
    """Prefer stripped HTML (usually richer/structured) but fall back to plain text."""
    if raw.html_body:
        soup = BeautifulSoup(raw.html_body, "html.parser")
        return soup.get_text(separator="\n", strip=True)
    return raw.text_body


class BaseExtractor(ABC):
    source: str

    @abstractmethod
    def parse(self, raw: RawEmail) -> ExtractedJob | ExtractionFailure:
        raise NotImplementedError

    def _first_url(self, raw: RawEmail, domain_hint: str) -> str | None:
        haystack = raw.html_body or raw.text_body
        urls = _URL_RE.findall(haystack)
        for url in urls:
            if domain_hint in url:
                return url.split('"')[0].rstrip(").,")
        return urls[0].rstrip(").,") if urls else None

    def _first_email(self, raw: RawEmail) -> str | None:
        haystack = raw.html_body or raw.text_body
        match = _EMAIL_RE.search(haystack)
        return match.group(0) if match else None


class CutshortParser(BaseExtractor):
    source = "cutshort"

    # Cutshort alert subjects look like: "Backend Engineer at Acme Corp"
    _SUBJECT_RE = re.compile(r"^(?P<title>.+?)\s+at\s+(?P<company>.+)$", re.IGNORECASE)

    def parse(self, raw: RawEmail) -> ExtractedJob | ExtractionFailure:
        body_text = _best_text(raw)
        match = self._SUBJECT_RE.match(raw.subject.strip())
        if not match:
            return ExtractionFailure(
                reason=REQUIRED_FIELDS_MISSING,
                raw_subject=raw.subject,
                raw_sender=raw.sender,
                raw_snippet=raw.snippet,
            )

        title = match.group("title").strip()
        company = match.group("company").strip()
        if not title or not company:
            return ExtractionFailure(
                reason=REQUIRED_FIELDS_MISSING,
                raw_subject=raw.subject,
                raw_sender=raw.sender,
                raw_snippet=raw.snippet,
            )

        return ExtractedJob(
            company=company,
            job_title=title,
            job_url=self._first_url(raw, "cutshort.io"),
            source=self.source,
            location=_find_labeled_value(body_text, ["Location"]),
            salary_text=_find_labeled_value(body_text, ["Salary", "Compensation", "CTC"]),
            experience_text=_find_labeled_value(body_text, ["Experience"]),
            job_description=body_text,
            recruiter_email=self._first_email(raw),
            received_at=raw.received_at,
        )


class InstahyreParser(BaseExtractor):
    source = "instahyre"

    # Instahyre alert subjects look like: "New match: Backend Developer - Acme Corp"
    _SUBJECT_RE = re.compile(
        r"^(?:New match:\s*)?(?P<title>.+?)\s*[-–]\s*(?P<company>.+)$", re.IGNORECASE
    )

    def parse(self, raw: RawEmail) -> ExtractedJob | ExtractionFailure:
        body_text = _best_text(raw)
        match = self._SUBJECT_RE.match(raw.subject.strip())
        if not match:
            return ExtractionFailure(
                reason=REQUIRED_FIELDS_MISSING,
                raw_subject=raw.subject,
                raw_sender=raw.sender,
                raw_snippet=raw.snippet,
            )

        title = match.group("title").strip()
        company = match.group("company").strip()
        if not title or not company:
            return ExtractionFailure(
                reason=REQUIRED_FIELDS_MISSING,
                raw_subject=raw.subject,
                raw_sender=raw.sender,
                raw_snippet=raw.snippet,
            )

        return ExtractedJob(
            company=company,
            job_title=title,
            job_url=self._first_url(raw, "instahyre.com"),
            source=self.source,
            location=_find_labeled_value(body_text, ["Location"]),
            salary_text=_find_labeled_value(body_text, ["Salary", "Compensation", "CTC"]),
            experience_text=_find_labeled_value(body_text, ["Experience"]),
            job_description=body_text,
            recruiter_email=self._first_email(raw),
            received_at=raw.received_at,
        )


def _find_labeled_value(body_text: str, labels: list[str]) -> str | None:
    """Looks for lines like 'Location: Bangalore' or 'Experience - 2-4 years'."""
    for label in labels:
        pattern = re.compile(rf"{re.escape(label)}\s*[:\-]\s*(.+)", re.IGNORECASE)
        match = pattern.search(body_text)
        if match:
            return match.group(1).strip().splitlines()[0].strip()
    return None


EXTRACTORS: dict[str, BaseExtractor] = {
    "cutshort": CutshortParser(),
    "instahyre": InstahyreParser(),
}


def extract_job(raw: RawEmail) -> ExtractedJob | ExtractionFailure:
    extractor = EXTRACTORS.get(raw.source)
    if extractor is None:
        return ExtractionFailure(
            reason=f"No extractor registered for source '{raw.source}'",
            raw_subject=raw.subject,
            raw_sender=raw.sender,
            raw_snippet=raw.snippet,
        )
    return extractor.parse(raw)
