"""Stage 9: no real network calls — providers are faked by subclassing
BaseLLMProvider and overriding _complete(). Focus is on schema validation,
hallucination filtering, and the retry/fallback contract."""
import json

import pytest

from app.config import load_profile
from app.schemas.job import NormalizedJob
from app.services.ai import (
    BaseLLMProvider,
    LLMResponseError,
    safe_analyze_job,
    safe_generate_message,
)

PROFILE = load_profile("profile.yaml")

VALID_ANALYSIS = {
    "match_score": 88,
    "priority": "STRONG",
    "technical_match": 90,
    "experience_match": 100,
    "salary_match": 80,
    "backend_relevance": 95,
    "matched_skills": ["Python", "Django"],
    "missing_skills": ["Kafka"],
    "red_flags": [],
    "why_good_fit": "Strong overlap on Python/Django with in-range experience.",
    "why_not_good_fit": "",
    "recommended_action": "APPLY",
}


class FakeProvider(BaseLLMProvider):
    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls = 0

    def _complete(self, prompt: str) -> str:
        self.calls += 1
        return self._responses.pop(0)


def _job() -> NormalizedJob:
    return NormalizedJob(company="Acme", job_title="Backend Engineer", source="cutshort")


def test_valid_json_response_parses_successfully():
    provider = FakeProvider([json.dumps(VALID_ANALYSIS)])
    analysis = provider.analyze_job(_job(), PROFILE)
    assert analysis.match_score == 88
    assert analysis.recommended_action == "APPLY"


def test_response_wrapped_in_markdown_fence_still_parses():
    fenced = "```json\n" + json.dumps(VALID_ANALYSIS) + "\n```"
    provider = FakeProvider([fenced])
    analysis = provider.analyze_job(_job(), PROFILE)
    assert analysis.match_score == 88


def test_malformed_json_raises_llm_response_error():
    provider = FakeProvider(["this is not json at all"])
    with pytest.raises(LLMResponseError):
        provider.analyze_job(_job(), PROFILE)


def test_missing_required_field_raises_llm_response_error():
    broken = dict(VALID_ANALYSIS)
    del broken["match_score"]
    provider = FakeProvider([json.dumps(broken)])
    with pytest.raises(LLMResponseError):
        provider.analyze_job(_job(), PROFILE)


def test_hallucinated_skill_is_filtered_out_of_matched_skills():
    hallucinated = dict(VALID_ANALYSIS)
    hallucinated["matched_skills"] = ["Python", "Kubernetes Certified Architect (fabricated)"]
    provider = FakeProvider([json.dumps(hallucinated)])
    analysis = provider.analyze_job(_job(), PROFILE)
    assert "Python" in analysis.matched_skills
    assert not any("fabricated" in s for s in analysis.matched_skills)


def test_safe_analyze_job_retries_once_then_falls_back_to_none():
    provider = FakeProvider(["not json", "still not json"])
    result = safe_analyze_job(provider, _job(), PROFILE)
    assert result is None
    assert provider.calls == 2


def test_safe_analyze_job_succeeds_on_second_attempt():
    provider = FakeProvider(["not json", json.dumps(VALID_ANALYSIS)])
    result = safe_analyze_job(provider, _job(), PROFILE)
    assert result is not None
    assert result.match_score == 88


def test_safe_generate_message_rejects_message_claiming_avoided_stack():
    provider = FakeProvider([
        "Hi, I have 5 years of Java and .NET experience...",
        "Hi, I'm Naveen, a Python backend engineer with Django experience...",
    ])
    message = safe_generate_message(provider, _job(), PROFILE, ["Python", "Django"])
    assert message is not None
    assert "Django" in message
    assert provider.calls == 2


def test_safe_generate_message_gives_up_after_two_bad_attempts():
    provider = FakeProvider([
        "Hi, I have deep Java expertise...",
        "Hi, I also love .NET development...",
    ])
    message = safe_generate_message(provider, _job(), PROFILE, ["Python"])
    assert message is None
