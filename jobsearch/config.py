"""Configuration models and loaders for environment and YAML settings."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import DoubleQuotedScalarString
import yaml


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
    SERP_API_KEY: str | None = Field(default=None)
    JINA_API_KEY: str | None = Field(default=None)
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    google_service_account_json: str = Field(
        default="",
        validation_alias="GOOGLE_SERVICE_ACCOUNT_JSON",
    )
    google_sheet_id: str = Field(
        default="",
        validation_alias="GOOGLE_SHEET_ID",
    )
    feedback_sync_schedule: str = Field(
        default="0 */4 * * 1-5",
        validation_alias="FEEDBACK_SYNC_SCHEDULE",
    )
    embedding_model: str = Field(
        default="text-embedding-3-large",
        validation_alias="EMBEDDING_MODEL",
    )
    chroma_path: str = Field(default="data/chroma", validation_alias="CHROMA_PATH")


class ScraperConfig(BaseModel):
    """Structured scraper configuration loaded from config.yaml."""

    sources: list[str] = Field(default_factory=list)
    search_terms: list[str]
    locations: list[str]
    remote_only: bool
    results_wanted_per_source: int
    ats_map_path: str = "config/company_ats_map.yaml"
    ats_concurrency: int = 10
    llm_parser_concurrency: int = 5


class ScoringConfig(BaseModel):
    """Structured scoring configuration loaded from config.yaml."""

    growth_default: int
    e_verify_employers: list[str]


class AlertsConfig(BaseModel):
    """Structured alert configuration loaded from config.yaml."""

    tier_threshold: str
    chat_id: str


class CompanyATSConfig(BaseModel):
    """Structured company ATS configuration for direct board scraping."""

    name: str
    ats: Literal["lever", "greenhouse", "ashby", "workday", "unknown"]
    slug: str
    tier: int
    discovered: bool | None = None
    verified: bool | None = None


class CompanyATSMap(BaseModel):
    """Container for company-to-ATS mappings loaded from YAML."""

    companies: list[CompanyATSConfig]


class AppConfig(BaseModel):
    """Container for the full YAML configuration payload."""

    scraper: ScraperConfig
    scoring: ScoringConfig
    alerts: AlertsConfig


_ATS_MAP_YAML = YAML()
_ATS_MAP_YAML.preserve_quotes = True
_ATS_MAP_YAML.indent(mapping=2, sequence=4, offset=2)


def get_settings() -> Settings:
    """Return the application settings instance."""

    return _get_settings()


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    """Load the YAML application configuration from disk."""

    return _load_config(Path(path))


def load_company_ats_map(path: str | Path | None = None) -> CompanyATSMap:
    """Load the company ATS map from disk."""

    resolved_path = get_company_ats_map_path(path)
    return _load_company_ats_map(resolved_path)


def get_company_ats_map_path(path: str | Path | None = None) -> Path:
    """Resolve the ATS map path from an override or the app config."""

    return Path(path) if path is not None else Path(load_config().scraper.ats_map_path)


def clear_company_ats_map_cache() -> None:
    """Clear the cached ATS map loader so writes are visible immediately."""

    _load_company_ats_map.cache_clear()


def load_company_ats_map_document(path: str | Path | None = None) -> tuple[Path, CommentedMap]:
    """Load the ruamel YAML document used for append-preserving ATS map writes."""

    resolved_path = get_company_ats_map_path(path)
    with resolved_path.open("r", encoding="utf-8") as file:
        raw_document = _ATS_MAP_YAML.load(file)

    if raw_document is None:
        raw_document = CommentedMap()
    if not isinstance(raw_document, CommentedMap):
        raise TypeError("ATS map YAML must be a mapping")

    companies = raw_document.get("companies")
    if companies is None:
        companies = CommentedSeq()
        raw_document["companies"] = companies
    if not isinstance(companies, CommentedSeq):
        raise TypeError("ATS map YAML 'companies' value must be a sequence")

    return (resolved_path, raw_document)


def build_company_ats_entry(
    *,
    name: str,
    ats: str,
    slug: str,
    tier: int,
    discovered: bool,
    verified: bool,
) -> CommentedMap:
    """Build one ATS map row using the existing key order and quote style."""

    entry = CommentedMap()
    entry["name"] = DoubleQuotedScalarString(name)
    entry["ats"] = DoubleQuotedScalarString(ats)
    entry["slug"] = DoubleQuotedScalarString(slug)
    entry["tier"] = tier
    entry["discovered"] = discovered
    entry["verified"] = verified
    return entry


def append_company_ats_entries(
    entries: Sequence[Mapping[str, Any]],
    path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Append new ATS map rows by lowercase slug without touching existing rows."""

    if not entries:
        return []

    resolved_path, document = load_company_ats_map_document(path)
    companies = document["companies"]
    existing_slugs = {str(company.get("slug", "")).lower() for company in companies if isinstance(company, Mapping)}

    appended: list[dict[str, Any]] = []
    for entry in entries:
        slug = str(entry.get("slug", "")).strip()
        slug_key = slug.lower()
        if not slug or slug_key in existing_slugs:
            continue

        companies.append(
            build_company_ats_entry(
                name=str(entry.get("name", slug)),
                ats=str(entry.get("ats", "unknown")),
                slug=slug,
                tier=int(entry.get("tier", 99)),
                discovered=bool(entry.get("discovered", True)),
                verified=bool(entry.get("verified", False)),
            )
        )
        existing_slugs.add(slug_key)
        appended.append(
            {
                "name": str(entry.get("name", slug)),
                "ats": str(entry.get("ats", "unknown")),
                "slug": slug,
                "tier": int(entry.get("tier", 99)),
                "discovered": bool(entry.get("discovered", True)),
                "verified": bool(entry.get("verified", False)),
            }
        )

    if appended:
        save_company_ats_map_document(document, resolved_path)

    return appended


def save_company_ats_map_document(document: CommentedMap, path: str | Path) -> None:
    """Persist an ATS map YAML document and clear the cached Pydantic loader."""

    resolved_path = Path(path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    with resolved_path.open("w", encoding="utf-8") as file:
        _ATS_MAP_YAML.dump(document, file)
    clear_company_ats_map_cache()


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


@lru_cache(maxsize=None)
def _load_company_ats_map(path: Path) -> CompanyATSMap:
    """Build and cache the company ATS mapping configuration."""

    with path.open("r", encoding="utf-8") as file:
        raw_config = yaml.safe_load(file) or {}

    expanded_config = _expand_env_vars(raw_config)
    return CompanyATSMap.model_validate(expanded_config)


def _expand_env_vars(value: Any) -> Any:
    """Recursively expand ${VAR} placeholders inside config values."""

    if isinstance(value, dict):
        return {key: _expand_env_vars(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env_vars(item) for item in value]
    if isinstance(value, str):
        return os.path.expandvars(value)
    return value
