"""LLM integration, kept fully behind an abstract provider so swapping models
or vendors is a one-line .env change (LLM_PROVIDER=gemini|anthropic).

Two responsibilities:
  1. analyze_job() — qualitative JD analysis (matched/missing skills narrative,
     red flags, why-fit, recommended action). The numeric match_score/priority
     it returns are advisory only; matcher.py's deterministic score is what
     gets stored (see matcher.py docstring).
  2. generate_message() — a short, JD-grounded recruiter message, only ever
     called for jobs that already cleared the score >= 80 bar.

Both prompts embed the candidate profile verbatim and explicitly forbid the
model from inventing skills/experience not present in it — the response is
then validated against LLMJobAnalysis and any hallucinated skill is filtered
out in code, not trusted from the model's say-so.
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod

from pydantic import ValidationError

from app.config import CandidateProfile, Settings
from app.schemas.job import LLMJobAnalysis, NormalizedJob

logger = logging.getLogger(__name__)

ANALYSIS_JSON_SHAPE = """{
  "match_score": 0,
  "priority": "HIGH",
  "technical_match": 0,
  "experience_match": 0,
  "salary_match": 0,
  "backend_relevance": 0,
  "matched_skills": [],
  "missing_skills": [],
  "red_flags": [],
  "why_good_fit": "",
  "why_not_good_fit": "",
  "recommended_action": "APPLY"
}"""


def _profile_block(profile: CandidateProfile) -> str:
    """The literal facts the model is allowed to reason from — nothing else."""
    return (
        f"Name: {profile.name}\n"
        f"Total experience: {profile.experience_years} years\n"
        f"Core skills: {', '.join(profile.core_skills)}\n"
        f"Bonus/additional skills: {', '.join(profile.bonus_skills)}\n"
        f"Target titles: {', '.join(profile.target_titles)}\n"
        f"Salary target: minimum acceptable {profile.salary.min_acceptable_lpa} LPA, "
        f"preferred {profile.salary.preferred_lpa}+ LPA ({profile.salary.currency})\n"
        f"Preferred locations/modes: {', '.join(profile.locations)}\n"
    )


def _job_block(job: NormalizedJob) -> str:
    return (
        f"Company: {job.company}\n"
        f"Title: {job.job_title}\n"
        f"Location: {job.location or 'unspecified'} ({job.work_mode.value})\n"
        f"Salary: {job.salary_min}-{job.salary_max} {job.currency}\n"
        f"Experience required: {job.experience_min}-{job.experience_max} years\n"
        f"Stated skills: {', '.join(job.required_skills) or 'none stated'}\n"
        f"Job description:\n{job.job_description[:4000]}\n"
    )


def build_analysis_prompt(job: NormalizedJob, profile: CandidateProfile) -> str:
    return f"""You are helping a job seeker evaluate a job posting against their real profile.

CANDIDATE PROFILE (the ONLY facts you may use about the candidate — never assume,
recall, or invent any skill, years of experience, or credential not listed here):
{_profile_block(profile)}

JOB POSTING:
{_job_block(job)}

Analyze the fit and respond with ONLY a JSON object matching exactly this shape
(no markdown fences, no commentary before or after):
{ANALYSIS_JSON_SHAPE}

Rules:
- matched_skills/missing_skills must only reference skills mentioned in the job posting.
- Never attribute a skill to the candidate unless it appears in the candidate profile above.
- why_good_fit and why_not_good_fit should be 1-2 concrete sentences each, referencing
  specifics from the job posting (not generic filler).
- recommended_action must be one of: APPLY, CONSIDER, SKIP.
"""


def build_message_prompt(job: NormalizedJob, profile: CandidateProfile, matched_skills: list[str]) -> str:
    return f"""Write a short, professional recruiter outreach message from the candidate below.

CANDIDATE PROFILE (only use facts listed here — never claim a skill or years of
experience not present in this profile):
{_profile_block(profile)}

JOB POSTING:
{_job_block(job)}

Skills confirmed to overlap between the candidate and this job: {', '.join(matched_skills) or 'general backend overlap'}

Write 4-6 sentences in this shape (fill in real specifics from the job posting,
don't use generic filler like "great opportunity"):

Hi [Recruiter Name],

I'm {profile.name.split()[0]}, a Python Backend Engineer with {profile.experience_years}+ years of
experience building production backend systems using [candidate's actual relevant skills].
My experience with [specific relevant technology from the JD] aligns well with this role,
particularly around [one specific responsibility mentioned in the JD].
I'd be interested in exploring the opportunity and would be happy to discuss my experience further.

Thanks,
{profile.name}

Return ONLY the message text, no extra commentary, no markdown.
"""


class LLMResponseError(RuntimeError):
    """Raised when the LLM's response can't be parsed/validated. Callers should
    catch this and fall back to deterministic-only scoring rather than crash."""


class BaseLLMProvider(ABC):
    @abstractmethod
    def _complete(self, prompt: str) -> str:
        """Returns raw text completion for the given prompt."""
        raise NotImplementedError

    def analyze_job(self, job: NormalizedJob, profile: CandidateProfile) -> LLMJobAnalysis:
        prompt = build_analysis_prompt(job, profile)
        raw_text = self._complete(prompt)
        return self._parse_analysis(raw_text, profile)

    def generate_message(self, job: NormalizedJob, profile: CandidateProfile, matched_skills: list[str]) -> str:
        prompt = build_message_prompt(job, profile, matched_skills)
        return self._complete(prompt).strip()

    @staticmethod
    def _parse_analysis(raw_text: str, profile: CandidateProfile) -> LLMJobAnalysis:
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            cleaned = cleaned.split("\n", 1)[-1] if cleaned.lower().startswith("json") else cleaned

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise LLMResponseError(f"LLM did not return valid JSON: {exc}") from exc

        try:
            analysis = LLMJobAnalysis.model_validate(data)
        except ValidationError as exc:
            raise LLMResponseError(f"LLM response failed schema validation: {exc}") from exc

        # Guard against hallucinated candidate skills: matched_skills may only
        # contain skills actually present in the candidate profile.
        allowed = profile.all_skills_lower
        analysis.matched_skills = [s for s in analysis.matched_skills if s.lower() in allowed]

        return analysis


class GeminiProvider(BaseLLMProvider):
    def __init__(self, api_key: str, model: str):
        import google.generativeai as genai

        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(model)

    def _complete(self, prompt: str) -> str:
        response = self._model.generate_content(prompt)
        return response.text


class AnthropicProvider(BaseLLMProvider):
    def __init__(self, api_key: str, model: str):
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def _complete(self, prompt: str) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in response.content if block.type == "text")


def get_llm_provider(settings: Settings) -> BaseLLMProvider:
    if settings.llm_provider == "gemini":
        return GeminiProvider(settings.gemini_api_key, settings.gemini_model)
    if settings.llm_provider == "anthropic":
        return AnthropicProvider(settings.anthropic_api_key, settings.anthropic_model)
    raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider!r}")


def safe_analyze_job(
    provider: BaseLLMProvider, job: NormalizedJob, profile: CandidateProfile
) -> LLMJobAnalysis | None:
    """Analyze with one retry on failure; returns None (never raises) if both attempts fail,
    so the pipeline falls back to deterministic-only scoring instead of crashing."""
    for attempt in range(2):
        try:
            return provider.analyze_job(job, profile)
        except LLMResponseError as exc:
            logger.warning("ai.analysis_failed: %s", exc)
        except Exception as exc:  # noqa: BLE001 — any provider/network failure must not crash the pipeline
            logger.warning("ai.provider_error: %s", exc)
    return None


def safe_generate_message(
    provider: BaseLLMProvider,
    job: NormalizedJob,
    profile: CandidateProfile,
    matched_skills: list[str],
) -> str | None:
    """Generates a message, guarding against skills outside the profile leaking in.
    Regenerates once on a guard failure, then gives up (caller falls back to a
    safe generic template) rather than sending an unverified claim."""
    for attempt in range(2):
        try:
            message = provider.generate_message(job, profile, matched_skills)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ai.message_generation_failed", extra={"attempt": attempt, "error": str(exc)})
            continue
        if _message_only_claims_known_skills(message, profile):
            return message
        logger.warning("ai.message_claimed_unknown_skill", extra={"attempt": attempt})
    return None


def _message_only_claims_known_skills(message: str, profile: CandidateProfile) -> bool:
    """Best-effort guard: flags the message if it mentions a skill from our
    'other backend stacks to avoid' list (a strong signal of a hallucinated claim)."""
    message_lower = message.lower()
    for stack in profile.avoid.other_backend_stacks:
        if stack.lower() in message_lower:
            return False
    return True
