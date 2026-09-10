import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models.job import Job
from app.schemas.job import NormalizedJob
from app.services.deduplicator import find_duplicate


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as s:
        yield s


def _job(**overrides) -> NormalizedJob:
    base = dict(
        company="Acme Technologies",
        job_title="Backend Engineer",
        job_url="https://cutshort.io/jobs/1",
        source="cutshort",
        location="Bangalore",
    )
    base.update(overrides)
    return NormalizedJob(**base)


def test_exact_url_match(session):
    existing = Job(company="Acme Technologies", job_title="Backend Engineer", job_url="https://cutshort.io/jobs/1", source="cutshort")
    session.add(existing)
    session.commit()

    incoming = _job(company="Acme Tech (renamed)", job_title="Sr. Backend Engineer")  # same URL, different text
    found = find_duplicate(session, incoming)
    assert found is not None and found.id == existing.id


def test_normalized_company_title_match_ignores_case_and_punctuation(session):
    existing = Job(company="Acme Technologies", job_title="Backend Engineer", source="cutshort")
    session.add(existing)
    session.commit()

    incoming = _job(company="ACME-Technologies!", job_title="backend engineer", job_url=None, location="Pune")
    found = find_duplicate(session, incoming)
    assert found is not None and found.id == existing.id


def test_company_title_location_match(session):
    existing = Job(company="Beta Corp", job_title="Django Developer", location="Bangalore", source="instahyre")
    session.add(existing)
    session.commit()

    # different company/title normalization would collide only if we also matched location
    incoming = _job(company="Beta Corp", job_title="Django Developer", job_url=None, location="Bangalore")
    found = find_duplicate(session, incoming)
    assert found is not None and found.id == existing.id


def test_fuzzy_match_catches_near_duplicate_titles(session):
    existing = Job(company="Zenith Labs", job_title="Backend Developer (Python/Django)", source="instahyre")
    session.add(existing)
    session.commit()

    incoming = _job(company="Zenith Labs", job_title="Backend Developer Python Django", job_url=None, location=None)
    found = find_duplicate(session, incoming, fuzzy_threshold=80.0)
    assert found is not None and found.id == existing.id


def test_no_match_for_genuinely_different_job(session):
    existing = Job(company="Zenith Labs", job_title="Backend Developer", source="instahyre")
    session.add(existing)
    session.commit()

    incoming = _job(company="Totally Different Co", job_title="Data Scientist", job_url=None, location=None)
    found = find_duplicate(session, incoming)
    assert found is None


def test_no_duplicate_record_created_across_two_source_alerts(session):
    """The same job arriving via both Cutshort and Instahyre must resolve to one row."""
    first = Job(company="Acme Technologies", job_title="Backend Engineer", job_url="https://cutshort.io/jobs/1", source="cutshort")
    session.add(first)
    session.commit()

    incoming_from_instahyre = _job(
        company="Acme Technologies", job_title="Backend Engineer",
        job_url="https://instahyre.com/job/acme-999", source="instahyre",
    )
    found = find_duplicate(session, incoming_from_instahyre)
    assert found is not None and found.id == first.id
