"""The four worked examples from the spec are the acceptance criteria for the
scoring algorithm — everything else in matcher.py exists to make these come out
in the right range without hardcoding them."""
from app.config import load_profile
from app.models.job import Priority, WorkMode
from app.schemas.job import NormalizedJob
from app.services.matcher import priority_for_score, score_job

PROFILE = load_profile("profile.yaml")


def _job(**overrides) -> NormalizedJob:
    base = dict(
        company="TestCo",
        job_title="Backend Engineer",
        source="cutshort",
        location="Bangalore",
        work_mode=WorkMode.ONSITE,
        currency="INR",
        required_skills=[],
        preferred_skills=[],
        job_description="",
    )
    base.update(overrides)
    return NormalizedJob(**base)


def test_senior_fastapi_postgres_scores_90_plus():
    job = _job(
        job_title="Senior Backend Engineer",
        required_skills=["Python", "FastAPI", "PostgreSQL"],
        experience_min=2, experience_max=5,
        salary_min=20, salary_max=30,
        job_description="Own backend services using Python, FastAPI and PostgreSQL for our platform.",
    )
    result = score_job(job, PROFILE)
    assert result.match_score >= 90, result.match_reason
    assert result.priority == Priority.HIGH


def test_django_backend_developer_scores_85_plus():
    job = _job(
        job_title="Backend Developer",
        required_skills=["Python", "Django"],
        experience_min=2, experience_max=4,
        salary_min=12, salary_max=16,
        job_description="Build REST APIs with Python and Django for our product engineering team.",
    )
    result = score_job(job, PROFILE)
    assert result.match_score >= 85, result.match_reason
    assert result.priority in {Priority.HIGH, Priority.STRONG}


def test_fullstack_django_react_scores_60_to_75():
    job = _job(
        job_title="Full Stack Engineer",
        required_skills=["Python", "Django", "React"],
        experience_min=2, experience_max=4,
        salary_min=10, salary_max=14,
        job_description="Build features end-to-end using Django on the backend and React on the frontend.",
    )
    result = score_job(job, PROFILE)
    assert 60 <= result.match_score <= 75, result.match_reason


def test_senior_python_django_6plus_years_scores_lower_for_experience():
    job = _job(
        job_title="Senior Python Engineer",
        required_skills=["Python", "Django"],
        experience_min=6, experience_max=None,
        salary_min=20, salary_max=20,
        job_description="Senior engineer role requiring deep Python and Django expertise.",
    )
    result = score_job(job, PROFILE)
    # Not auto-rejected for saying "Senior" — but experience gap must pull the score down
    # relative to an equivalent in-range role.
    in_range_job = _job(
        job_title="Senior Python Engineer",
        required_skills=["Python", "Django"],
        experience_min=2, experience_max=5,
        salary_min=20, salary_max=20,
        job_description="Senior engineer role requiring deep Python and Django expertise.",
    )
    in_range_result = score_job(in_range_job, PROFILE)
    assert result.match_score < in_range_result.match_score
    assert "experience" in result.match_reason.lower() or "years" in result.match_reason.lower()


def test_title_says_senior_alone_is_not_penalized():
    """Spec: do not automatically reject/downscore purely because the title says 'Senior'."""
    senior_in_range = _job(
        job_title="Senior Backend Engineer",
        required_skills=["Python", "Django"],
        experience_min=2, experience_max=4,
        salary_min=15, salary_max=20,
    )
    non_senior_in_range = _job(
        job_title="Backend Engineer",
        required_skills=["Python", "Django"],
        experience_min=2, experience_max=4,
        salary_min=15, salary_max=20,
    )
    senior_result = score_job(senior_in_range, PROFILE)
    plain_result = score_job(non_senior_in_range, PROFILE)
    assert senior_result.match_score == plain_result.match_score


def test_priority_thresholds_match_spec():
    assert priority_for_score(95) == Priority.HIGH
    assert priority_for_score(90) == Priority.HIGH
    assert priority_for_score(85) == Priority.STRONG
    assert priority_for_score(80) == Priority.STRONG
    assert priority_for_score(75) == Priority.CONSIDER
    assert priority_for_score(70) == Priority.CONSIDER
    assert priority_for_score(65) == Priority.LOW
    assert priority_for_score(60) == Priority.LOW
    assert priority_for_score(59) == Priority.SKIP
    assert priority_for_score(0) == Priority.SKIP


def test_low_salary_job_flagged_explicitly_in_reason():
    job = _job(
        required_skills=["Python", "Django"],
        experience_min=2, experience_max=4,
        salary_min=5, salary_max=7,
    )
    result = score_job(job, PROFILE)
    assert "below" in result.match_reason.lower() or "salary" in result.match_reason.lower()


def test_pure_frontend_role_scores_low():
    job = _job(
        job_title="Frontend Developer",
        required_skills=["React", "JavaScript"],
        experience_min=2, experience_max=4,
        salary_min=12, salary_max=16,
    )
    result = score_job(job, PROFILE)
    assert result.match_score < 60


def test_java_title_is_hard_skip():
    job = _job(
        job_title="Backend Developer - Java",
        required_skills=["Java", "Spring"],
        experience_min=2, experience_max=4,
        salary_min=15, salary_max=20,
    )
    result = score_job(job, PROFILE)
    assert result.match_score <= 40
    assert result.priority == Priority.SKIP
    assert "java" in result.match_reason.lower()


def test_dotnet_or_javascript_skills_are_hard_skip():
    dotnet = score_job(
        _job(job_title="Software Engineer", required_skills=[".NET", "C#"]),
        PROFILE,
    )
    js = score_job(
        _job(job_title="Software Engineer", required_skills=["JavaScript", "Node.js"]),
        PROFILE,
    )
    assert dotnet.match_score <= 40 and dotnet.priority == Priority.SKIP
    assert js.match_score <= 40 and js.priority == Priority.SKIP


def test_python_role_not_skipped_just_because_jd_mentions_json():
    job = _job(
        job_title="Python Backend Engineer",
        required_skills=["Python", "Django", "PostgreSQL"],
        experience_min=2, experience_max=4,
        salary_min=15, salary_max=20,
        job_description="Build JSON APIs with Django REST Framework.",
    )
    result = score_job(job, PROFILE)
    assert result.match_score >= 80
    assert "avoided stack" not in result.match_reason.lower()
