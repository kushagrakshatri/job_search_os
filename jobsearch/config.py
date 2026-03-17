"""Configuration models and loaders for environment and YAML settings."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict
import yaml

TITLE_PREFILTER_TERMS: tuple[str, ...] = (
    "ai",
    "ml",
    "machine learning",
    "llm",
    "nlp",
    "engineer",
    "swe",
    "software",
)


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    TELEGRAM_TOKEN: str = ""
    CHAT_ID: str = ""
    SCORER_MODEL: str = "gpt-5.4"
    DAILY_SCORE_LIMIT: int = 150
    SCRAPE_SCHEDULE_MORNING: str = "0 8 * * 1-5"
    SCRAPE_SCHEDULE_EVENING: str = "0 18 * * 1-5"
    SCHEDULER_TIMEZONE: str = "America/Los_Angeles"


class ScraperConfig(BaseModel):
    """Structured scraper configuration loaded from config.yaml."""

    sources: list[str]
    search_terms: list[str]
    locations: list[str]
    remote_only: bool
    results_wanted_per_source: int


class ScoringConfig(BaseModel):
    """Structured scoring configuration loaded from config.yaml."""

    growth_default: int
    e_verify_employers: list[str]


class AlertsConfig(BaseModel):
    """Structured alert configuration loaded from config.yaml."""

    tier_threshold: str
    chat_id: str


class AppConfig(BaseModel):
    """Container for the full YAML configuration payload."""

    scraper: ScraperConfig
    scoring: ScoringConfig
    alerts: AlertsConfig


def get_settings() -> Settings:
    """Return the application settings instance."""

    return _get_settings()


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    """Load the YAML application configuration from disk."""

    return _load_config(Path(path))


@lru_cache(maxsize=1)
def _get_settings() -> Settings:
    """Build and cache the settings object."""

    return Settings()


@lru_cache(maxsize=None)
def _load_config(path: Path) -> AppConfig:
    """Build and cache the application YAML configuration."""

    with path.open("r", encoding="utf-8") as file:
        raw_config = yaml.safe_load(file) or {}

    expanded_config = _expand_env_vars(raw_config)
    return AppConfig.model_validate(expanded_config)


def _expand_env_vars(value: Any) -> Any:
    """Recursively expand ${VAR} placeholders inside config values."""

    if isinstance(value, dict):
        return {key: _expand_env_vars(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env_vars(item) for item in value]
    if isinstance(value, str):
        return os.path.expandvars(value)
    return value
