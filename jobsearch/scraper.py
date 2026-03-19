"""Job scraping, filtering, deduplication, and persistence."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
from typing import Any

import structlog
from sqlalchemy import select

from jobsearch.config import get_settings, load_company_ats_map, load_config
from jobsearch.db import get_db
from jobsearch.models import Job

logger = structlog.get_logger(__name__)

_LAST_RUN_METRICS: dict[str, int] = {
    "found": 0,
    "passed_filters": 0,
    "new_inserted": 0,
}
_SENIORITY_FILTER_TERMS: tuple[str, ...] = (
    "senior",
    "staff",
    "principal",
    "director",
    "manager",
    "lead",
    "head of",
    "vp ",
    "vice president",
    "partner",
    "intern",
    "internship",
    "co-op",
    "coop",
    "co op",
    "phd",
    "postdoc",
    "post-doc",
    "residency",
)
_EARLY_CAREER_EXEMPTION_TERMS: tuple[str, ...] = (
    "new grad",
    "entry",
    "junior",
    "associate",
    "university grad",
    "campus",
)
_US_LOCATION_MARKERS: tuple[str, ...] = (
    "united states",
    "usa",
    " us ",
    ", ca",
    ", ny",
    ", tx",
    ", wa",
    ", ma",
    ", il",
    ", co",
    ", ga",
    ", fl",
    ", nc",
    ", va",
    ", or",
    ", az",
    ", nv",
    ", ut",
    ", mn",
    ", oh",
    ", mi",
    ", pa",
    ", nj",
    ", ct",
    ", md",
    ", wi",
    ", mo",
    "san francisco",
    "new york",
    "seattle",
    "boston",
    "austin",
    "chicago",
    "los angeles",
    "denver",
    "atlanta",
    "remote",
)


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


def _utcnow() -> datetime:
    """Return the current UTC time as a naive datetime."""

    return datetime.now(timezone.utc).replace(tzinfo=None)


def _normalize_string(value: Any, default: str = "") -> str:
    """Convert a possibly missing scalar value into a stripped string."""

    if value is None:
        return default

    text = str(value).strip()
    return text or default


def _compute_url_hash(url: str) -> str:
    """Compute the database URL hash for a job URL."""

    return sha256(url.encode("utf-8")).hexdigest()[:16]


def _compute_slug_hash(company: str, title: str) -> str:
    """Compute the database slug hash for a company-title pair."""

    slug_value = f"{company.lower().strip()}|{title.lower().strip()}"
    return sha256(slug_value.encode("utf-8")).hexdigest()[:16]


def _is_remote_location(location: str | None) -> bool:
    """Determine whether a normalized location should be treated as remote."""

    if location is None:
        return True
    return "remote" in location.lower()


def _passes_location_gate(job: Job) -> bool:
    """Return whether a job location appears eligible for US-based search."""

    if job.is_remote:
        return True

    normalized_location = job.location.strip().lower()
    if not normalized_location:
        return True

    padded_location = f" {normalized_location} "
    return any(
        marker in padded_location if marker == " us " else marker in normalized_location
        for marker in _US_LOCATION_MARKERS
    )


def _passes_seniority_gate(title: str) -> bool:
    """Filter out clearly senior or non-target early-career titles."""

    normalized_title = title.lower()
    is_senior = any(term in normalized_title for term in _SENIORITY_FILTER_TERMS)
    has_exemption = any(term in normalized_title for term in _EARLY_CAREER_EXEMPTION_TERMS)
    return not is_senior or has_exemption


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


def build_external_job(
    *,
    title: Any,
    company: Any,
    location: Any,
    url: Any,
    description: Any,
    source: str,
    scraped_at: datetime | None = None,
) -> Job | None:
    """Build a Job ORM object for any non-jobspy discovery source."""

    normalized_title = _normalize_string(title)
    normalized_url = _normalize_string(url)
    if not normalized_title or not normalized_url:
        return None

    normalized_company = _normalize_string(company, default="Unknown Company")
    normalized_location = _normalize_string(location, default="Remote")
    url_hash = _compute_url_hash(normalized_url)
    cleaned_description = clean_jd_text(description)
    if not cleaned_description:
        raw_description = _normalize_string(description)
        if raw_description:
            cleaned_description = raw_description[:2000]

    return Job(
        id=url_hash,
        title=normalized_title,
        company=normalized_company,
        location=normalized_location,
        is_remote=_is_remote_location(location if isinstance(location, str) else normalized_location),
        url=normalized_url,
        url_hash=url_hash,
        slug_hash=_compute_slug_hash(normalized_company, normalized_title),
        source=_normalize_string(source, default="unknown"),
        scraped_at=scraped_at or _utcnow(),
        jd_text=cleaned_description,
        knocked_out=False,
        llm_scored=False,
        embedding_computed=False,
    )


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


def get_last_run_metrics() -> dict[str, int]:
    """Return the most recent scrape metrics for /scan."""

    return dict(_LAST_RUN_METRICS)


def _record_run_metrics(*, found: int, passed_filters: int, new_inserted: int) -> None:
    """Persist in-memory scrape metrics for bot reporting."""

    _LAST_RUN_METRICS.update(
        {
            "found": found,
            "passed_filters": passed_filters,
            "new_inserted": new_inserted,
        }
    )


def _workday_fallback_targets() -> list[tuple[str, str]]:
    """Build the Workday fallback targets from the ATS company map."""

    targets: list[tuple[str, str]] = []
    for company in load_company_ats_map().companies:
        if company.ats != "workday" or company.slug == "TODO":
            continue
        targets.append((f"https://{company.slug}.myworkdayjobs.com/", company.name))
    return targets


async def _discover_jobs(search_terms: Sequence[str]) -> list[Job]:
    """Run the non-jobspy discovery stack and return unstaged jobs."""

    del search_terms  # The new discovery layers use fixed ATS and Serp queries.

    settings = get_settings()
    companies = [company.model_dump() for company in load_company_ats_map().companies]

    from jobsearch.scrapers.ats_scraper import scrape_ats, select_companies_for_configured_ats_run
    from jobsearch.scrapers.llm_parser import parse_urls
    from jobsearch.scrapers.serp_scraper import scrape_serp

    companies = select_companies_for_configured_ats_run(companies)
    ats_task = asyncio.create_task(scrape_ats(companies))

    serp_jobs: list[Job] = []
    serp_fallback_urls: list[str] = []
    if settings.SERP_API_KEY:
        ats_jobs, serp_result = await asyncio.gather(
            ats_task,
            asyncio.create_task(scrape_serp(settings.SERP_API_KEY)),
        )
        serp_jobs, serp_fallback_urls = serp_result
    else:
        logger.warning(
            "serp_discovery_skipped",
            source="serp",
            company="SERP_API_KEY",
            error="SERP_API_KEY not set — skipping open-web discovery",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        ats_jobs = await ats_task

    parse_tasks: list[asyncio.Future[list[Job]] | asyncio.Task[list[Job]]] = []
    if serp_fallback_urls:
        parse_tasks.append(asyncio.create_task(parse_urls(serp_fallback_urls)))
    for url, company_name in _workday_fallback_targets():
        parse_tasks.append(asyncio.create_task(parse_urls([url], company_hint=company_name)))

    llm_jobs: list[Job] = []
    if parse_tasks:
        parse_results = await asyncio.gather(*parse_tasks)
        for batch in parse_results:
            llm_jobs.extend(batch)

    return [*ats_jobs, *serp_jobs, *llm_jobs]


def fetch_all(
    sources: Sequence[str] | None,
    search_terms: Sequence[str],
    limit_per_source: int,
) -> list[Job]:
    """Fetch, clean, deduplicate, and persist newly discovered jobs."""

    del sources
    del limit_per_source

    _record_run_metrics(found=0, passed_filters=0, new_inserted=0)

    discovered_jobs = asyncio.run(_discover_jobs(search_terms))
    unique_jobs = deduplicate_jobs(discovered_jobs)
    location_passed_jobs = [job for job in unique_jobs if _passes_location_gate(job)]
    seniority_passed_jobs = [job for job in location_passed_jobs if _passes_seniority_gate(job.title)]

    logger.info(
        "location_filtered",
        count=len(unique_jobs) - len(location_passed_jobs),
    )
    logger.info(
        "ingest_filters",
        total=len(unique_jobs),
        passed_location=len(location_passed_jobs),
        passed_seniority=len(seniority_passed_jobs),
        final_passed=len(seniority_passed_jobs),
    )

    with get_db() as session:
        existing_rows = session.execute(select(Job.url_hash, Job.slug_hash)).all()
        existing_url_hashes = {row[0] for row in existing_rows}
        existing_slug_hashes = {row[1] for row in existing_rows}

        new_jobs: list[Job] = []
        for job in seniority_passed_jobs:
            if job.url_hash in existing_url_hashes or job.slug_hash in existing_slug_hashes:
                continue

            session.add(job)
            new_jobs.append(job)
            existing_url_hashes.add(job.url_hash)
            existing_slug_hashes.add(job.slug_hash)

        session.commit()

    _record_run_metrics(
        found=len(discovered_jobs),
        passed_filters=len(seniority_passed_jobs),
        new_inserted=len(new_jobs),
    )
    logger.info(
        "scrape_run",
        sources=[],
        search_terms=list(search_terms),
        total_fetched=len(discovered_jobs),
        passed_filters=len(seniority_passed_jobs),
        new_inserted=len(new_jobs),
    )
    return new_jobs


def main() -> list[Job]:
    """Run the scraper against the configured discovery sources."""

    app_config = load_config()
    return fetch_all(
        sources=app_config.scraper.sources,
        search_terms=app_config.scraper.search_terms,
        limit_per_source=app_config.scraper.results_wanted_per_source,
    )


if __name__ == "__main__":
    main()
