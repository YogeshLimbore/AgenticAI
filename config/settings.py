"""
config/settings.py — Typed, validated configuration with Pydantic
All settings load from .env or environment variables.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Credentials ───────────────────────────────
    naukri_email: str = Field(default="", description="Naukri.com login email")
    naukri_password: str = Field(default="", description="Naukri.com password")
    credential_store: str = Field(default="env")  # "env" | "keyring"

    # ── LLM (Gemini free tier) ────────────────────
    gemini_api_key: str = Field(default="", description="Google AI Studio free API key")
    # gemini-3-flash-preview: FREE — 15 req/min, 1M tokens/day
    gemini_model: str = Field(default="gemini-3-flash-preview")
    llm_cache_ttl_hours: int = Field(default=24)

    # ── Job search ────────────────────────────────
    job_keywords: List[str] = Field(default=[
        "Data Scientist",
        "Machine Learning Engineer",
        "AI Engineer",
        "ML Engineer",
        "Data Analyst",
        "NLP Engineer",
        "Deep Learning Engineer",
    ])
    job_location: str = Field(default="Pune")
    max_apply_per_run: int = Field(default=15, ge=1, le=50)
    experience_yrs: str = Field(default="0-2")
    match_threshold: int = Field(default=60, ge=0, le=100)

    your_skills: List[str] = Field(default=[
        "Python", "Machine Learning", "Deep Learning",
        "TensorFlow", "PyTorch", "Scikit-learn",
        "Pandas", "NumPy", "SQL", "Data Analysis",
        "Natural Language Processing", "Computer Vision", "Statistics",
    ])

    # ── Application form defaults ─────────────────
    current_ctc: str = Field(default="4")
    expected_ctc: str = Field(default="6")
    notice_period: str = Field(default="30")
    willing_to_relocate: str = Field(default="Yes")
    total_experience: str = Field(default="1")
    current_location: str = Field(default="Pune")
    preferred_location: str = Field(default="Pune")

    # ── Scheduler ─────────────────────────────────
    schedule_time: str = Field(default="")  # "09:00" or empty to disable

    # ── Notifications ─────────────────────────────
    telegram_bot_token: str = Field(default="")
    telegram_chat_id: str = Field(default="")

    # ── Paths ─────────────────────────────────────
    base_dir: Path = Field(default=Path("."))

    @property
    def log_dir(self) -> Path:
        p = self.base_dir / "logs"
        p.mkdir(exist_ok=True)
        return p

    @property
    def debug_dir(self) -> Path:
        p = self.base_dir / "debug_pages"
        p.mkdir(exist_ok=True)
        return p

    @property
    def memory_dir(self) -> Path:
        p = self.base_dir / "memory"
        p.mkdir(exist_ok=True)
        return p

    @property
    def db_path(self) -> Path:
        return self.memory_dir / "agent.db"

    @property
    def llm_cache_path(self) -> Path:
        return self.memory_dir / "llm_cache.json"

    @field_validator("match_threshold")
    @classmethod
    def validate_threshold(cls, v: int) -> int:
        if not 0 <= v <= 100:
            raise ValueError("match_threshold must be 0-100")
        return v

    def validate_required(self) -> list[str]:
        """Return list of missing required fields."""
        errors = []
        if not self.naukri_email:
            errors.append("NAUKRI_EMAIL")
        if not self.naukri_password:
            errors.append("NAUKRI_PASSWORD")
        if not self.gemini_api_key:
            errors.append("GEMINI_API_KEY")
        return errors


# Singleton
_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
