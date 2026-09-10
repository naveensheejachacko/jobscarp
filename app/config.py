"""Application configuration: environment settings + the static candidate profile.

Settings come from `.env` (via pydantic-settings). The candidate profile is a
separate, hand-edited YAML file (`profile.yaml`) — it's personal resume data,
not runtime config, so it gets its own loader and its own Pydantic model.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "postgresql+psycopg://jobauto:jobauto@localhost:5432/jobauto"

    # Gmail
    gmail_credentials_path: str = "./credentials.json"
    gmail_token_path: str = "./token.json"
    gmail_query_cutshort: str = "from:(cutshort.io)"
    gmail_query_instahyre: str = "from:(instahyre.com)"
    gmail_query_linkedin: str = "from:(linkedin.com) (subject:job OR subject:jobs OR subject:hiring)"
    gmail_query_naukri: str = "from:(naukri.com)"

    # Google Sheets
    google_sheet_id: str = ""

    # LLM
    llm_provider: str = "gemini"  # "gemini" | "anthropic" | "deepseek"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5"
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"

    # Notifications
    notify_min_score: int = 90
    notify_email_to: str = ""

    # Scheduler
    process_interval_minutes: int = 30

    # Misc
    log_level: str = "INFO"
    profile_path: str = "./profile.yaml"


class SalaryTarget(BaseModel):
    currency: str = "INR"
    min_acceptable_lpa: float
    preferred_lpa: float


class ExperienceRange(BaseModel):
    min_years: float
    max_years: float


class AvoidRules(BaseModel):
    primary_frontend: bool = True
    react_primary: bool = True
    other_backend_stacks: list[str] = Field(default_factory=list)
    min_experience_hard_ceiling_years: float = 6
    internships: bool = True
    very_low_salary: bool = True


class CandidateProfile(BaseModel):
    """The candidate's static facts. The matcher and every LLM prompt read only
    from an instance of this model — never from training data or model recall."""

    name: str
    experience_years: float
    target_titles: list[str]
    core_skills: list[str]
    bonus_skills: list[str] = Field(default_factory=list)
    salary: SalaryTarget
    experience_range: ExperienceRange
    locations: list[str] = Field(default_factory=list)
    work_modes: list[str] = Field(default_factory=list)
    avoid: AvoidRules = Field(default_factory=AvoidRules)
    preferences: list[str] = Field(default_factory=list)

    @property
    def all_skills(self) -> list[str]:
        return [*self.core_skills, *self.bonus_skills]

    @property
    def all_skills_lower(self) -> set[str]:
        return {s.lower() for s in self.all_skills}


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_profile(path: str | Path | None = None) -> CandidateProfile:
    settings = get_settings()
    profile_path = Path(path or settings.profile_path)
    if not profile_path.exists():
        raise FileNotFoundError(
            f"Candidate profile not found at {profile_path}. "
            "Copy/edit profile.yaml at the project root."
        )
    with profile_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return CandidateProfile.model_validate(raw)


@lru_cache
def get_profile() -> CandidateProfile:
    return load_profile()
