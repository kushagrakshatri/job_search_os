"""Job scraping, filtering, deduplication, and persistence."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from hashlib import sha256
from html.parser import HTMLParser
from typing import Any

import pandas as pd
import structlog
from jobspy import scrape_jobs
from sqlalchemy import select

from jobsearch.config import TITLE_PREFILTER_TERMS, load_config
from jobsearch.db import get_db
from jobsearch.models import Job

logger = structlog.get_logger(__name__)


class _HTMLStripper(HTMLParser):
    """Convert HTML fragments into plain text."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        """Collect text nodes from the HTML input."""

        self._parts.append(data)

    def get_text(self) -> str:
        """Return the concatenated stripped text."""

        return " ".join(self._parts)


def _normalize_string(value: Any, default: str = "") -> str:
    """Convert a possibly missing scalar value into a stripped string."""

    if value is None or pd.isna(value):
        return default
    return str(value).strip()


def _normalize_bool(value: Any) -> bool:
    """Convert a possibly missing scalar value into a boolean."""

    if value is None or pd.isna(value):
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes"}


def _compute_url_hash(url: str) -> str:
    """Compute the database URL hash for a job URL."""

    return sha256(url.encode("utf-8")).hexdigest()[:16]


def _compute_slug_hash(company: str, title: str) -> str:
    """Compute the database slug hash for a company-title pair."""

    slug_value = f"{company.lower().strip()}|{title.lower().strip()}"
    return sha256(slug_value.encode("utf-8")).hexdigest()[:16]


def _build_job(row: dict[str, Any], scraped_at: datetime) -> Job | None:
    """Convert a jobspy row into a Job ORM object."""

    title = _normalize_string(row.get("title"))
    if not title_matches_prefilter(title):
        return None

    url = _normalize_string(row.get("job_url"))
    if not url:
        return None

    company = _normalize_string(row.get("company"), default="Unknown Company")
    location = _normalize_string(row.get("location"), default="Unknown Location")
    jd_text = clean_jd_text(row.get("description"))

    return Job(
        title=title,
        company=company,
        location=location,
        is_remote=_normalize_bool(row.get("is_remote")),
        url=url,
        url_hash=_compute_url_hash(url),
        slug_hash=_compute_slug_hash(company, title),
        source=_normalize_string(row.get("site")),
        scraped_at=scraped_at,
        jd_text=jd_text,
        knocked_out=False,
        llm_scored=False,
    )


def title_matches_prefilter(title: str) -> bool:
    """Apply the coarse title pre-filter before LLM scoring."""

    normalized_title = title.lower()
    return any(term in normalized_title for term in TITLE_PREFILTER_TERMS)


def clean_jd_text(raw_text: Any) -> str:
    """Clean and truncate raw job description text."""

    text = _normalize_string(raw_text)
    if not text:
        return ""

    stripper = _HTMLStripper()
    stripper.feed(text)
    stripped = stripper.get_text()
    collapsed = " ".join(stripped.split())
    return collapsed[:4000]


def deduplicate_jobs(jobs: Sequence[Job]) -> list[Job]:
    """Remove jobs that collide on URL or slug hashes."""

    seen_url_hashes: set[str] = set()
    seen_slug_hashes: set[str] = set()
    unique_jobs: list[Job] = []

    for job in jobs:
        if job.url_hash in seen_url_hashes or job.slug_hash in seen_slug_hashes:
            continue

        seen_url_hashes.add(job.url_hash)
        seen_slug_hashes.add(job.slug_hash)
        unique_jobs.append(job)

    return unique_jobs


def fetch_all(
    sources: Sequence[str],
    search_terms: Sequence[str],
    limit_per_source: int,
) -> list[Job]:
    """Fetch, clean, deduplicate, and persist newly discovered jobs."""

    app_config = load_config()
    location = app_config.scraper.locations[0] if app_config.scraper.locations else None
    remote_only = app_config.scraper.remote_only

    total_fetched = 0
    scraped_at = datetime.utcnow()
    staged_jobs: list[Job] = []

    for source in sources:
        for search_term in search_terms:
            try:
                jobs_df = scrape_jobs(
                    site_name=source,
                    search_term=search_term,
                    location=location,
                    is_remote=remote_only,
                    results_wanted=limit_per_source,
                    description_format="html",
                )
            except Exception as exc:
                logger.warning(
                    "scrape_jobs_failed",
                    source=source,
                    search_term=search_term,
                    error=str(exc),
                )
                continue

            if jobs_df is None or jobs_df.empty:
                continue

            total_fetched += len(jobs_df.index)

            for row in jobs_df.to_dict(orient="records"):
                job = _build_job(row, scraped_at=scraped_at)
                if job is not None:
                    staged_jobs.append(job)

    unique_jobs = deduplicate_jobs(staged_jobs)

    with get_db() as session:
        existing_rows = session.execute(select(Job.url_hash, Job.slug_hash)).all()
        existing_url_hashes = {row[0] for row in existing_rows}
        existing_slug_hashes = {row[1] for row in existing_rows}

        new_jobs: list[Job] = []
        for job in unique_jobs:
            if job.url_hash in existing_url_hashes or job.slug_hash in existing_slug_hashes:
                continue

            session.add(job)
            new_jobs.append(job)
            existing_url_hashes.add(job.url_hash)
            existing_slug_hashes.add(job.slug_hash)

        session.commit()

    logger.info(
        "scrape_run",
        sources=list(sources),
        search_terms=list(search_terms),
        total_fetched=total_fetched,
        new_inserted=len(new_jobs),
    )
    return new_jobs


def main() -> list[Job]:
    """Run the scraper against configured sources and search terms."""

    app_config = load_config()
    return fetch_all(
        sources=app_config.scraper.sources,
        search_terms=app_config.scraper.search_terms,
        limit_per_source=5,
    )


if __name__ == "__main__":
    main()
