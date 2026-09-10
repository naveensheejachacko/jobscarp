from datetime import timedelta

from app.api.jobs import get_llm_provider_dependency
from app.db import utcnow
from app.main import app as fastapi_app
from app.models.job import Job, JobStatus, Priority


def _seed_job(session_factory, **overrides) -> int:
    with session_factory() as session:
        job = Job(
            company=overrides.pop("company", "Acme"),
            job_title=overrides.pop("job_title", "Backend Engineer"),
            source=overrides.pop("source", "cutshort"),
            status=overrides.pop("status", JobStatus.NEW),
            match_score=overrides.pop("match_score", None),
            priority=overrides.pop("priority", None),
            required_skills=overrides.pop("required_skills", []),
            missing_skills=overrides.pop("missing_skills", []),
            red_flags=overrides.pop("red_flags", []),
        )
        for k, v in overrides.items():
            setattr(job, k, v)
        session.add(job)
        session.commit()
        return job.id


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_list_jobs_empty(client):
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_jobs_filters_by_min_score(client, session_factory):
    _seed_job(session_factory, company="Low", match_score=50, priority=Priority.SKIP)
    _seed_job(session_factory, company="High", match_score=92, priority=Priority.HIGH)

    resp = client.get("/jobs", params={"min_score": 80})
    assert resp.status_code == 200
    companies = [j["company"] for j in resp.json()]
    assert companies == ["High"]


def test_list_jobs_filters_by_source_and_status(client, session_factory):
    _seed_job(session_factory, company="A", source="cutshort", status=JobStatus.NEW)
    _seed_job(session_factory, company="B", source="instahyre", status=JobStatus.APPLIED)

    resp = client.get("/jobs", params={"source": "instahyre"})
    assert [j["company"] for j in resp.json()] == ["B"]

    resp = client.get("/jobs", params={"status": "APPLIED"})
    assert [j["company"] for j in resp.json()] == ["B"]


def test_get_job_by_id(client, session_factory):
    job_id = _seed_job(session_factory, company="Zenith")
    resp = client.get(f"/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["company"] == "Zenith"


def test_get_job_404(client):
    resp = client.get("/jobs/9999")
    assert resp.status_code == 404


def test_high_priority_endpoint_filters_by_priority(client, session_factory):
    _seed_job(session_factory, company="Skip", match_score=50, priority=Priority.SKIP)
    _seed_job(session_factory, company="Strong", match_score=85, priority=Priority.STRONG)
    _seed_job(session_factory, company="High", match_score=95, priority=Priority.HIGH)

    resp = client.get("/jobs/high-priority")
    companies = {j["company"] for j in resp.json()}
    assert companies == {"Strong", "High"}


def test_stats_endpoint(client, session_factory):
    _seed_job(session_factory, company="A", match_score=95, priority=Priority.HIGH, status=JobStatus.NEW, source="cutshort")
    _seed_job(session_factory, company="B", match_score=60, priority=Priority.LOW, status=JobStatus.SKIPPED, source="instahyre")

    resp = client.get("/jobs/stats")
    data = resp.json()
    assert data["total_jobs"] == 2
    assert data["high_priority_count"] == 1
    assert data["by_source"] == {"cutshort": 1, "instahyre": 1}


def test_patch_status_transitions_and_records_event(client, session_factory):
    job_id = _seed_job(session_factory, company="Acme", status=JobStatus.NEW)

    resp = client.patch(f"/jobs/{job_id}/status", json={"status": "SHORTLISTED"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "SHORTLISTED"

    resp = client.patch(f"/jobs/{job_id}/status", json={"status": "APPLIED", "notes": "Applied via portal"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "APPLIED"
    assert body["notes"] == "Applied via portal"


def test_generate_message_rejects_low_score_job(client, session_factory):
    job_id = _seed_job(session_factory, company="Acme", match_score=50, priority=Priority.SKIP)
    resp = client.post(f"/jobs/{job_id}/generate-message")
    assert resp.status_code == 400


def test_generate_message_succeeds_for_high_score_job_with_fake_provider(client, session_factory):
    job_id = _seed_job(
        session_factory, company="Acme", match_score=88, priority=Priority.STRONG,
        required_skills=["Python", "Django"],
    )

    class FakeProvider:
        def generate_message(self, job, profile, matched_skills):
            return "Hi Recruiter, I'm Naveen, a Python/Django backend engineer..."

    fastapi_app.dependency_overrides[get_llm_provider_dependency] = lambda: FakeProvider()
    try:
        resp = client.post(f"/jobs/{job_id}/generate-message")
    finally:
        fastapi_app.dependency_overrides.pop(get_llm_provider_dependency, None)

    assert resp.status_code == 200
    assert "Naveen" in resp.json()["application_message"]


def test_generate_message_falls_back_to_template_when_no_provider(client, session_factory):
    job_id = _seed_job(
        session_factory, company="Acme", match_score=88, priority=Priority.STRONG,
        required_skills=["Python", "Django"],
    )
    fastapi_app.dependency_overrides[get_llm_provider_dependency] = lambda: None
    try:
        resp = client.post(f"/jobs/{job_id}/generate-message")
    finally:
        fastapi_app.dependency_overrides.pop(get_llm_provider_dependency, None)

    assert resp.status_code == 200
    assert resp.json()["application_message"]  # fallback template, non-empty


def test_applications_endpoint_excludes_new_jobs(client, session_factory):
    _seed_job(session_factory, company="Untouched", status=JobStatus.NEW)
    _seed_job(session_factory, company="InProgress", status=JobStatus.APPLIED)

    resp = client.get("/applications")
    companies = [j["company"] for j in resp.json()]
    assert companies == ["InProgress"]


def test_followups_endpoint_returns_only_due_dates(client, session_factory):
    _seed_job(session_factory, company="Future", follow_up_date=utcnow() + timedelta(days=5))
    _seed_job(session_factory, company="Due", follow_up_date=utcnow() - timedelta(days=1))

    resp = client.get("/applications/follow-ups")
    companies = [j["company"] for j in resp.json()]
    assert companies == ["Due"]
