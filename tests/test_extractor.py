from pathlib import Path

from app.schemas.job import ExtractedJob, ExtractionFailure, RawEmail
from app.services.extractor import CutshortParser, InstahyreParser, extract_job

FIXTURES = Path(__file__).parent / "fixtures"


def _load_html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_cutshort_parser_extracts_all_fields():
    raw = RawEmail(
        message_id="m1",
        source="cutshort",
        subject="Backend Engineer at Acme Technologies",
        sender="alerts@cutshort.io",
        html_body=_load_html("cutshort_job_alert.html"),
    )
    result = CutshortParser().parse(raw)

    assert isinstance(result, ExtractedJob)
    assert result.company == "Acme Technologies"
    assert result.job_title == "Backend Engineer"
    assert result.location == "Bangalore"
    assert result.experience_text == "2-5 years"
    assert result.salary_text == "18-28 LPA"
    assert result.job_url == "https://cutshort.io/jobs/backend-engineer-acme-12345"
    assert result.recruiter_email == "recruiter@acmetech.com"
    assert "FastAPI" in result.job_description


def test_instahyre_parser_extracts_all_fields():
    raw = RawEmail(
        message_id="m2",
        source="instahyre",
        subject="Backend Developer - Zenith Labs",
        sender="alerts@instahyre.com",
        html_body=_load_html("instahyre_job_alert.html"),
    )
    result = InstahyreParser().parse(raw)

    assert isinstance(result, ExtractedJob)
    assert result.company == "Zenith Labs"
    assert result.job_title == "Backend Developer"
    assert result.location == "Remote"
    assert result.experience_text == "2-4 years"
    assert result.salary_text == "12-16 LPA"
    assert "instahyre.com/job/backend-developer-zenith-98765" in result.job_url
    assert result.recruiter_email == "priya@zenithlabs.io"


def test_malformed_subject_returns_extraction_failure_not_a_guess():
    raw = RawEmail(
        message_id="m3",
        source="cutshort",
        subject="Check out this week's top picks!",  # no "<title> at <company>" shape
        sender="alerts@cutshort.io",
        snippet="weekly digest",
        html_body="<html><body>Some digest content with no job structure.</body></html>",
    )
    result = CutshortParser().parse(raw)

    assert isinstance(result, ExtractionFailure)
    assert result.raw_subject == raw.subject
    assert result.raw_sender == raw.sender


def test_extract_job_dispatches_by_source():
    raw = RawEmail(
        message_id="m4",
        source="instahyre",
        subject="Backend Developer - Zenith Labs",
        sender="alerts@instahyre.com",
        html_body=_load_html("instahyre_job_alert.html"),
    )
    result = extract_job(raw)
    assert isinstance(result, ExtractedJob)
    assert result.source == "instahyre"


def test_extract_job_unknown_source_fails_safely():
    raw = RawEmail(
        message_id="m5",
        source="linkedin",  # not registered yet
        subject="Some Job - Some Co",
        sender="jobs@linkedin.com",
        snippet="n/a",
    )
    result = extract_job(raw)
    assert isinstance(result, ExtractionFailure)
    assert "linkedin" in result.reason
