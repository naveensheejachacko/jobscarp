"""Turns the raw, free-text fields an extractor produced into canonical types
the matcher and DB expect: numeric salary/experience ranges, a work-mode enum,
and a deduped lowercase-normalized skills list.
"""
from __future__ import annotations

import re

from app.models.job import WorkMode
from app.schemas.job import ExtractedJob, NormalizedJob

# A reasonably complete backend skill vocabulary to scan the JD text for. Keeping
# this list explicit (rather than NLP/keyword-soup) keeps skill matching auditable.
KNOWN_SKILLS = [
    "Python", "Django", "Django REST Framework", "DRF", "FastAPI", "PostgreSQL",
    "Postgres", "MySQL", "MongoDB", "Redis", "Celery", "Docker", "Kubernetes",
    "AWS", "ECS", "Fargate", "ECR", "EC2", "S3", "IAM", "Lambda", "GCP", "Azure",
    "REST API", "REST APIs", "GraphQL", "WebSockets", "Elasticsearch", "Kafka",
    "RabbitMQ", "JWT", "OAuth", "Microservices", "System Design", "React",
    "Angular", "Vue", "Node.js", "Node", "Java", "Spring", ".NET", "C#", "Golang",
    "Go", "Ruby", "Rails", "PHP", "Laravel", "CI/CD", "Jenkins", "Terraform",
    "Nginx", "gRPC", "SQL", "NoSQL", "Payment integration", "Payment integrations",
    "Third-party API integrations", "Background processing",
]

# Distinct casing variants so "REST API" and "REST APIs" both match one canonical skill,
# and title-case is preserved when re-displaying a matched skill.
_SKILL_PATTERN = re.compile(
    r"(?<!\w)(" + "|".join(re.escape(s) for s in sorted(KNOWN_SKILLS, key=len, reverse=True)) + r")(?!\w)",
    re.IGNORECASE,
)

_LPA_RANGE_RE = re.compile(
    r"(?P<min>\d+(?:\.\d+)?)\s*[-–to]+\s*(?P<max>\d+(?:\.\d+)?)\s*(?:lpa|lakh|lakhs)?", re.IGNORECASE
)
_LPA_SINGLE_RE = re.compile(r"(?P<val>\d+(?:\.\d+)?)\s*(?:lpa|lakh|lakhs)", re.IGNORECASE)

_YEARS_RANGE_RE = re.compile(r"(?P<min>\d+(?:\.\d+)?)\s*[-–to]+\s*(?P<max>\d+(?:\.\d+)?)\s*\+?\s*year")
_YEARS_PLUS_RE = re.compile(r"(?P<val>\d+(?:\.\d+)?)\s*\+\s*year")
_YEARS_SINGLE_RE = re.compile(r"(?P<val>\d+(?:\.\d+)?)\s*year")

_REMOTE_RE = re.compile(r"\bremote\b", re.IGNORECASE)
_HYBRID_RE = re.compile(r"\bhybrid\b", re.IGNORECASE)


def normalize_salary(text: str | None) -> tuple[float | None, float | None]:
    """'12-18 LPA' -> (12.0, 18.0); '20 LPA' -> (20.0, 20.0); None/unparseable -> (None, None).

    Values are assumed to already be annual INR lakhs (LPA), the standard unit
    used by Indian job boards/alert emails.
    """
    if not text:
        return None, None

    range_match = _LPA_RANGE_RE.search(text)
    if range_match:
        return float(range_match.group("min")), float(range_match.group("max"))

    single_match = _LPA_SINGLE_RE.search(text)
    if single_match:
        val = float(single_match.group("val"))
        return val, val

    # Bare numeric range with no unit suffix, e.g. "12-18"
    bare_range = re.search(r"(?P<min>\d+(?:\.\d+)?)\s*[-–]\s*(?P<max>\d+(?:\.\d+)?)", text)
    if bare_range:
        return float(bare_range.group("min")), float(bare_range.group("max"))

    return None, None


def normalize_experience(text: str | None) -> tuple[float | None, float | None]:
    """'2-5 years' -> (2.0, 5.0); '6+ years' -> (6.0, None); '3 years' -> (3.0, 3.0)."""
    if not text:
        return None, None

    range_match = _YEARS_RANGE_RE.search(text)
    if range_match:
        return float(range_match.group("min")), float(range_match.group("max"))

    plus_match = _YEARS_PLUS_RE.search(text)
    if plus_match:
        return float(plus_match.group("val")), None

    single_match = _YEARS_SINGLE_RE.search(text)
    if single_match:
        val = float(single_match.group("val"))
        return val, val

    return None, None


def normalize_work_mode(location_text: str | None, job_description: str = "") -> WorkMode:
    haystack = f"{location_text or ''} {job_description}"
    if _REMOTE_RE.search(haystack):
        return WorkMode.REMOTE
    if _HYBRID_RE.search(haystack):
        return WorkMode.HYBRID
    if location_text:
        return WorkMode.ONSITE
    return WorkMode.UNKNOWN


def extract_skills(text: str) -> list[str]:
    """Scans free text for known skills, returns each canonical name once,
    in the casing it appears in KNOWN_SKILLS (not however the JD capitalized it)."""
    if not text:
        return []
    canonical_by_lower = {s.lower(): s for s in KNOWN_SKILLS}
    seen: dict[str, str] = {}
    for match in _SKILL_PATTERN.finditer(text):
        lower = match.group(0).lower()
        canonical = canonical_by_lower.get(lower, match.group(0))
        seen.setdefault(canonical.lower(), canonical)
    return list(seen.values())


def normalize_job(extracted: ExtractedJob) -> NormalizedJob:
    salary_min, salary_max = normalize_salary(extracted.salary_text)
    exp_min, exp_max = normalize_experience(extracted.experience_text)
    work_mode = normalize_work_mode(extracted.location, extracted.job_description)
    skills = extract_skills(f"{extracted.job_title}\n{extracted.job_description}")

    return NormalizedJob(
        company=extracted.company.strip(),
        job_title=extracted.job_title.strip(),
        job_url=extracted.job_url,
        source=extracted.source,
        location=extracted.location.strip() if extracted.location else None,
        work_mode=work_mode,
        salary_min=salary_min,
        salary_max=salary_max,
        currency="INR",
        experience_min=exp_min,
        experience_max=exp_max,
        required_skills=skills,
        preferred_skills=[],
        job_description=extracted.job_description,
        recruiter_name=extracted.recruiter_name,
        recruiter_email=extracted.recruiter_email,
        received_at=extracted.received_at,
    )
