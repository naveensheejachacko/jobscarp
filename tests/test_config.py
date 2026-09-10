from app.config import CandidateProfile, get_settings, load_profile


def test_settings_load_with_defaults():
    settings = get_settings()
    assert settings.llm_provider in {"gemini", "anthropic"}
    assert settings.process_interval_minutes > 0


def test_profile_loads_from_yaml():
    profile = load_profile("profile.yaml")
    assert isinstance(profile, CandidateProfile)
    assert profile.name == "Naveen Chacko"
    assert profile.experience_years == 2.5
    assert "Python" in profile.core_skills
    assert "Django" in profile.core_skills
    assert profile.salary.min_acceptable_lpa == 12
    assert profile.salary.preferred_lpa == 15


def test_profile_all_skills_combines_core_and_bonus():
    profile = load_profile("profile.yaml")
    assert "python" in profile.all_skills_lower
    assert "redis" in profile.all_skills_lower
    assert len(profile.all_skills) == len(profile.core_skills) + len(profile.bonus_skills)
