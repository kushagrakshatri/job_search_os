"""Direct ATS scrapers for known company job boards."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
import structlog

from jobsearch.config import CompanyATSConfig, load_config
from jobsearch.models import Job
from jobsearch.scraper import build_external_job

logger = structlog.get_logger(__name__)
_SLUG_ISSUES_PATH = Path("data/ats_slug_issues.txt")
_RUN_COUNT_PATH = Path("data/scrape_run_count.txt")


def _utc_timestamp() -> str:
    """Return the current UTC timestamp for structured logs."""

    return datetime.now(timezone.utc).isoformat()


def _company_value(company: CompanyATSConfig | dict[str, Any], field: str) -> Any:
    """Read a field from either a config model or a plain dictionary."""

    if isinstance(company, dict):
        return company.get(field)
    return getattr(company, field, None)


def _read_run_count() -> int:
    """Load the persisted ATS rotation counter, creating it if missing."""

    if not _RUN_COUNT_PATH.exists():
        _RUN_COUNT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _RUN_COUNT_PATH.write_text("0\n", encoding="utf-8")
        return 0

    try:
        return int(_RUN_COUNT_PATH.read_text(encoding="utf-8").strip() or "0")
    except ValueError:
        return 0


def _write_run_count(value: int) -> None:
    """Persist the ATS rotation counter."""

    _RUN_COUNT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _RUN_COUNT_PATH.write_text(f"{value}\n", encoding="utf-8")


def select_companies_for_configured_ats_run(
    companies: list[CompanyATSConfig | dict[str, Any]],
) -> list[CompanyATSConfig | dict[str, Any]]:
    """Select the companies to scrape for the configured ATS run."""

    run_count = _read_run_count()
    _write_run_count(run_count + 1)
    rotation_index = run_count % 3

    tier_99_companies: list[CompanyATSConfig | dict[str, Any]] = []
    for company in companies:
        try:
            tier = int(_company_value(company, "tier") or 0)
        except (TypeError, ValueError):
            tier = 0
        if tier == 99:
            tier_99_companies.append(company)

    selected_tier_99_slugs = {
        str(_company_value(company, "slug") or "").lower()
        for index, company in enumerate(
            sorted(
                tier_99_companies,
                key=lambda item: str(_company_value(item, "slug") or "").lower(),
            )
        )
        if index % 3 == rotation_index
    }

    selected_companies: list[CompanyATSConfig | dict[str, Any]] = []
    for company in companies:
        try:
            tier = int(_company_value(company, "tier") or 0)
        except (TypeError, ValueError):
            tier = 0

        if tier != 99:
            selected_companies.append(company)
            continue

        slug_key = str(_company_value(company, "slug") or "").lower()
        if slug_key in selected_tier_99_slugs:
            selected_companies.append(company)

    logger.info(
        "ats_tier99_rotation",
        run_count=run_count,
        rotation_index=rotation_index,
        total_tier99=len(tier_99_companies),
        selected_tier99=len(selected_tier_99_slugs),
    )
    return selected_companies


def _append_slug_issue(company_name: str) -> None:
    """Persist a company name for later ATS slug cleanup."""

    _SLUG_ISSUES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _SLUG_ISSUES_PATH.open("a", encoding="utf-8") as file:
        file.write(f"{company_name}\n")


def _log_warning(event: str, *, source: str, company: str, error: str) -> None:
    """Emit a structured warning with the required fields."""

    logger.warning(
        event,
        source=source,
        company=company,
        error=error,
        timestamp=_utc_timestamp(),
    )


def _log_greenhouse_html_sample(company_name: str, raw_html: str) -> None:
    """Log a single raw HTML sample for a known failing Greenhouse company."""

    if company_name.lower() not in {"apple", "netflix"} or not raw_html:
        return

    logger.info(
        "greenhouse_raw_html_sample",
        source="greenhouse",
        company=company_name,
        raw_html_sample=raw_html[:500],
        timestamp=_utc_timestamp(),
    )


async def _request_json(
    session: aiohttp.ClientSession,
    *,
    url: str,
    source: str,
    company_name: str,
    params: dict[str, str] | None = None,
) -> Any:
    """Issue one ATS request and decode the JSON response."""

    try:
        async with session.get(url, params=params) as response:
            if response.status == 404:
                message = f"{company_name} ({source}): slug not found — add to ats_slug_issues.txt"
                _log_warning(
                    "ats_slug_not_found",
                    source=source,
                    company=company_name,
                    error=message,
                )
                await asyncio.to_thread(_append_slug_issue, company_name)
                return None

            response.raise_for_status()
            return await response.json()
    except aiohttp.ClientResponseError as exc:
        _log_warning(
            "ats_company_scrape_failed",
            source=source,
            company=company_name,
            error=str(exc),
        )
    except asyncio.TimeoutError as exc:
        _log_warning(
            "ats_company_scrape_failed",
            source=source,
            company=company_name,
            error=f"timeout: {exc}",
        )
    except aiohttp.ClientError as exc:
        _log_warning(
            "ats_company_scrape_failed",
            source=source,
            company=company_name,
            error=str(exc),
        )

    return None


def _build_jobs(
    rows: list[dict[str, Any]],
    *,
    company_name: str,
    source: str,
    title_key: str,
    location_getter,
    description_getter,
    url_getter,
) -> list[Job]:
    """Convert ATS JSON rows into Job ORM objects."""

    jobs: list[Job] = []
    for row in rows:
        job = build_external_job(
            title=row.get(title_key),
            company=company_name,
            location=location_getter(row),
            url=url_getter(row),
            description=description_getter(row),
            source=source,
        )
        if job is not None:
            jobs.append(job)
    return jobs


def _lever_location(row: dict[str, Any]) -> str | None:
    """Extract the Lever location string."""

    categories = row.get("categories")
    if isinstance(categories, dict):
        return categories.get("location")
    return None


def _greenhouse_location(row: dict[str, Any]) -> str | None:
    """Extract the Greenhouse location string."""

    location = row.get("location")
    if isinstance(location, dict):
        return location.get("name")
    return None


def _greenhouse_description(row: dict[str, Any]) -> Any:
    """Extract the preferred Greenhouse description payload."""

    return row.get("content") or row.get("description")


def _ashby_location(row: dict[str, Any]) -> str | None:
    """Extract the Ashby location string."""

    location = row.get("location")
    if isinstance(location, dict):
        return location.get("name")
    if isinstance(location, str):
        return location
    location_name = row.get("locationName")
    if isinstance(location_name, str):
        return location_name
    return None


async def _fetch_lever(
    session: aiohttp.ClientSession,
    *,
    company_name: str,
    slug: str,
) -> list[Job]:
    """Fetch one Lever board."""

    payload = await _request_json(
        session,
        url=f"https://api.lever.co/v0/postings/{slug}?mode=json",
        source="lever",
        company_name=company_name,
    )
    if not isinstance(payload, list):
        return []

    return _build_jobs(
        payload,
        company_name=company_name,
        source="lever",
        title_key="text",
        location_getter=_lever_location,
        description_getter=lambda row: row.get("description"),
        url_getter=lambda row: row.get("hostedUrl"),
    )


async def _fetch_greenhouse(
    session: aiohttp.ClientSession,
    *,
    company_name: str,
    slug: str,
) -> list[Job]:
    """Fetch one Greenhouse board."""

    payload = await _request_json(
        session,
        url=f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
        source="greenhouse",
        company_name=company_name,
    )
    if not isinstance(payload, dict):
        return []

    rows = payload.get("jobs")
    if not isinstance(rows, list):
        return []

    for row in rows:
        raw_html = _greenhouse_description(row)
        if isinstance(raw_html, str) and raw_html:
            _log_greenhouse_html_sample(company_name, raw_html)
            break

    return _build_jobs(
        rows,
        company_name=company_name,
        source="greenhouse",
        title_key="title",
        location_getter=_greenhouse_location,
        description_getter=_greenhouse_description,
        url_getter=lambda row: row.get("absolute_url"),
    )


async def _fetch_ashby(
    session: aiohttp.ClientSession,
    *,
    company_name: str,
    slug: str,
) -> list[Job]:
    """Fetch one Ashby board."""

    payload = await _request_json(
        session,
        url=f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
        source="ashby",
        company_name=company_name,
    )
    if not isinstance(payload, dict):
        return []

    rows = payload.get("jobPostings")
    if not isinstance(rows, list):
        rows = payload.get("jobs")
    if not isinstance(rows, list):
        return []

    return _build_jobs(
        rows,
        company_name=company_name,
        source="ashby",
        title_key="title",
        location_getter=_ashby_location,
        description_getter=lambda row: row.get("descriptionHtml") or row.get("description", ""),
        url_getter=lambda row: row.get("jobUrl") or row.get("absolute_url"),
    )


async def _scrape_company(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    company: CompanyATSConfig | dict[str, Any],
) -> list[Job]:
    """Fetch one company board without allowing failures to bubble."""

    company_name = str(_company_value(company, "name") or "Unknown Company")
    ats = str(_company_value(company, "ats") or "unknown")
    slug = str(_company_value(company, "slug") or "")

    if ats in {"unknown", "workday"} or not slug or slug == "TODO":
        return []

    try:
        async with semaphore:
            if ats == "lever":
                return await _fetch_lever(session, company_name=company_name, slug=slug)
            if ats == "greenhouse":
                return await _fetch_greenhouse(session, company_name=company_name, slug=slug)
            if ats == "ashby":
                return await _fetch_ashby(session, company_name=company_name, slug=slug)
    except Exception as exc:  # pragma: no cover - defensive catch for run continuity
        _log_warning(
            "ats_company_scrape_failed",
            source=ats,
            company=company_name,
            error=str(exc),
        )

    return []


async def scrape_ats(companies: list[dict[str, Any]] | list[CompanyATSConfig]) -> list[Job]:
    """Scrape all supported direct ATS boards concurrently."""

    app_config = load_config()
    timeout = aiohttp.ClientTimeout(total=10)
    semaphore = asyncio.Semaphore(app_config.scraper.ats_concurrency)

    async with aiohttp.ClientSession(
        timeout=timeout,
        headers={"User-Agent": "Mozilla/5.0 JobSearchOS"},
    ) as session:
        tasks = [_scrape_company(session, semaphore, company) for company in companies]
        results = await asyncio.gather(*tasks)

    jobs: list[Job] = []
    for batch in results:
        jobs.extend(batch)
    return jobs
