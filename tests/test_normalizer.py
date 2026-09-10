from app.models.job import WorkMode
from app.schemas.job import ExtractedJob
from app.services.normalizer import (
    extract_skills,
    normalize_experience,
    normalize_job,
    normalize_salary,
    normalize_work_mode,
)


# --- salary ---

def test_normalize_salary_range_with_lpa_suffix():
    assert normalize_salary("18-28 LPA") == (18.0, 28.0)


def test_normalize_salary_single_value():
    assert normalize_salary("20 LPA") == (20.0, 20.0)


def test_normalize_salary_bare_range_no_unit():
    assert normalize_salary("12-16") == (12.0, 16.0)


def test_normalize_salary_none_input():
    assert normalize_salary(None) == (None, None)


def test_normalize_salary_unparseable():
    assert normalize_salary("Competitive, based on experience") == (None, None)


# --- experience ---

def test_normalize_experience_range():
    assert normalize_experience("2-5 years") == (2.0, 5.0)


def test_normalize_experience_plus():
    assert normalize_experience("6+ years") == (6.0, None)


def test_normalize_experience_single():
    assert normalize_experience("3 years") == (3.0, 3.0)


def test_normalize_experience_none():
    assert normalize_experience(None) == (None, None)


# --- work mode ---

def test_normalize_work_mode_remote():
    assert normalize_work_mode("Remote", "") == WorkMode.REMOTE


def test_normalize_work_mode_hybrid():
    assert normalize_work_mode("Bangalore (Hybrid)", "") == WorkMode.HYBRID


def test_normalize_work_mode_onsite_default():
    assert normalize_work_mode("Bangalore", "") == WorkMode.ONSITE


def test_normalize_work_mode_unknown_when_no_location():
    assert normalize_work_mode(None, "") == WorkMode.UNKNOWN


# --- skills ---

def test_extract_skills_finds_known_backend_skills():
    text = "Looking for Python, Django, FastAPI and PostgreSQL experience with Redis and Docker."
    skills = extract_skills(text)
    assert set(skills) >= {"Python", "Django", "FastAPI", "PostgreSQL", "Redis", "Docker"}


def test_extract_skills_no_duplicates_across_case_variants():
    text = "python and Python and PYTHON are all the same skill"
    assert extract_skills(text).count("Python") == 1


def test_extract_skills_empty_text():
    assert extract_skills("") == []


# --- full pipeline ---

def test_normalize_job_end_to_end():
    extracted = ExtractedJob(
        company="Acme Technologies",
        job_title="Backend Engineer",
        job_url="https://cutshort.io/jobs/1",
        source="cutshort",
        location="Bangalore",
        salary_text="18-28 LPA",
        experience_text="2-5 years",
        job_description="Python, Django, FastAPI, PostgreSQL, Redis, Celery, AWS ECS/Fargate",
    )
    normalized = normalize_job(extracted)

    assert normalized.salary_min == 18.0
    assert normalized.salary_max == 28.0
    assert normalized.experience_min == 2.0
    assert normalized.experience_max == 5.0
    assert normalized.work_mode == WorkMode.ONSITE
    assert "Python" in normalized.required_skills
    assert "FastAPI" in normalized.required_skills
