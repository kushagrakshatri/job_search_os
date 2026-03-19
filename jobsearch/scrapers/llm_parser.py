"""Jina reader plus Codex fallback parser for custom job pages."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlsplit

import aiohttp
import structlog

from jobsearch.codex_client import complete
from jobsearch.config import get_settings, load_config
from jobsearch.models import Job
from jobsearch.scraper import build_external_job

logger = structlog.get_logger(__name__)

PATH_PREFILTER_TERMS: tuple[str, ...] = (
    "engineer",
    "ml",
    "ai",
    "machine-learning",
    "llm",
    "platform",
    "infrastructure",
)
EXTRACTION_SYSTEM_PROMPT = (
    "You are a job posting extractor. Given markdown text from a careers page, "
    "extract all job postings. Return ONLY a JSON array. No explanation. No markdown "
    "fences. If no jobs found, return []. Each item: {title, location, url, description}"
)


def _utc_timestamp() -> str:
    """Return the current UTC timestamp for structured logs."""

    return datetime.now(timezone.utc).isoformat()


def _log_warning(event: str, *, company: str, error: str) -> None:
    """Emit a structured warning with the required fields."""

    logger.warning(
        event,
        source="llm_parsed",
        company=company,
        error=error,
        timestamp=_utc_timestamp(),
    )


def _should_process(url: str, company_hint: str = "") -> bool:
    """Apply the URL-path prefilter before any network call."""

    parts = urlsplit(url)
    path = parts.path.lower()
    if company_hint and "myworkdayjobs.com" in (parts.hostname or "").lower():
        return True
    return any(term in path for term in PATH_PREFILTER_TERMS)


def _jina_url(url: str) -> str:
    """Build the Jina reader URL for an origin page."""

    return f"https://r.jina.ai/{url}"


def _build_user_prompt(url: str, markdown: str) -> str:
    """Construct the extractor prompt for one fetched page."""

    return (
        "Extract job postings from this page. Return JSON array only.\n\n"
        f"Page: {url}\n"
        "Content (truncated to 3000 chars):\n"
        f"{markdown[:3000]}"
    )


def _infer_company_name(url: str) -> str:
    """Infer a fallback company name from a URL host."""

    host = urlsplit(url).hostname or ""
    labels = [
        label
        for label in host.split(".")
        if label and label not in {"www", "jobs", "careers", "boards", "job-boards"}
    ]
    if not labels:
        return "Unknown Company"
    company_slug = labels[0].replace("-", " ").replace("_", " ")
    return company_slug.title()


async def _fetch_markdown(
    session: aiohttp.ClientSession,
    *,
    url: str,
    company_name: str,
) -> str | None:
    """Fetch one page through Jina reader."""

    try:
        async with session.get(_jina_url(url)) as response:
            response.raise_for_status()
            return await response.text()
    except asyncio.TimeoutError as exc:
        _log_warning("jina_fetch_failed", company=company_name, error=f"timeout: {exc}")
    except aiohttp.ClientError as exc:
        _log_warning("jina_fetch_failed", company=company_name, error=str(exc))
    return None


async def _extract_payload(url: str, markdown: str, company_name: str) -> list[dict[str, Any]]:
    """Run the Codex extractor and parse the returned JSON array."""

    try:
        raw_response = await asyncio.to_thread(
            complete,
            EXTRACTION_SYSTEM_PROMPT,
            _build_user_prompt(url, markdown),
        )
    except RuntimeError as exc:
        _log_warning("llm_parse_failed", company=company_name, error=str(exc))
        return []

    if raw_response is None:
        _log_warning("llm_parse_failed", company=company_name, error="complete() returned None")
        return []

    try:
        payload = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        _log_warning("llm_parse_failed", company=company_name, error=str(exc))
        return []

    if not isinstance(payload, list):
        _log_warning("llm_parse_failed", company=company_name, error="extractor returned non-array JSON")
        return []

    return [row for row in payload if isinstance(row, dict)]


async def _parse_url(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    *,
    url: str,
    company_hint: str,
) -> list[Job]:
    """Process one fallback URL end to end."""

    company_name = company_hint or _infer_company_name(url)

    async with semaphore:
        markdown = await _fetch_markdown(session, url=url, company_name=company_name)
        if markdown is None:
            return []
        payload = await _extract_payload(url, markdown, company_name)

    jobs: list[Job] = []
    for row in payload:
        job = build_external_job(
            title=row.get("title"),
            company=company_name,
            location=row.get("location"),
            url=urljoin(url, str(row.get("url") or url)),
            description=row.get("description"),
            source="llm_parsed",
        )
        if job is not None:
            jobs.append(job)

    return jobs


async def parse_urls(urls: list[str], company_hint: str = "") -> list[Job]:
    """Parse custom or Workday URLs through Jina reader plus Codex extraction."""

    candidate_urls: list[str] = []
    seen_urls: set[str] = set()
    for url in urls:
        cleaned = url.strip()
        if not cleaned or cleaned in seen_urls or not _should_process(cleaned, company_hint):
            continue
        seen_urls.add(cleaned)
        candidate_urls.append(cleaned)

    if not candidate_urls:
        return []

    settings = get_settings()
    app_config = load_config()
    headers = {"User-Agent": "Mozilla/5.0 JobSearchOS"}
    if settings.JINA_API_KEY:
        headers["Authorization"] = f"Bearer {settings.JINA_API_KEY}"

    timeout = aiohttp.ClientTimeout(total=15)
    semaphore = asyncio.Semaphore(app_config.scraper.llm_parser_concurrency)

    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        results = await asyncio.gather(
            *[
                _parse_url(
                    session,
                    semaphore,
                    url=url,
                    company_hint=company_hint,
                )
                for url in candidate_urls
            ]
        )

    jobs: list[Job] = []
    for batch in results:
        jobs.extend(batch)
    return jobs
