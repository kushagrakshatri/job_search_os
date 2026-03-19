"""SerpAPI-powered open-web discovery for job boards."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import structlog
from serpapi import Client as SerpClient
from serpapi.exceptions import HTTPError as SerpHTTPError

from jobsearch.config import append_company_ats_entries, load_company_ats_map
from jobsearch.models import Job
from jobsearch.scrapers.ats_scraper import scrape_ats

logger = structlog.get_logger(__name__)

SERP_QUERIES: tuple[str, ...] = (
    '"AI Engineer" site:jobs.lever.co',
    '"Applied ML Engineer" site:jobs.lever.co',
    '"AI Platform Engineer" site:jobs.lever.co',
    '"Machine Learning Engineer" site:boards.greenhouse.io',
    '"AI Engineer" site:boards.greenhouse.io',
    '"LangGraph" OR "LangChain" OR "RAG" engineer site:jobs.lever.co',
    '"AI Infrastructure Engineer" site:boards.greenhouse.io',
    '"LLM Engineer" site:jobs.lever.co OR site:boards.greenhouse.io',
    '"AI Engineer" site:jobs.ashbyhq.com',
    '"Agentic AI" engineer site:jobs.lever.co OR site:boards.greenhouse.io',
)

SERP_TARGETED_QUERIES: tuple[str, ...] = (
    '"AI Platform Engineer" site:jobs.lever.co',
    '"LangGraph" OR "LangChain" OR "RAG" engineer site:jobs.lever.co',
    '"Agentic AI" engineer site:jobs.lever.co OR site:boards.greenhouse.io',
)

SERP_MINIMAL_QUERIES: tuple[str, ...] = (
    '"LangGraph" OR "LangChain" OR "RAG" engineer site:jobs.lever.co',
)


def _utc_timestamp() -> str:
    """Return the current UTC timestamp for structured logs."""

    return datetime.now(timezone.utc).isoformat()


def _log_warning(event: str, *, company: str, error: str) -> None:
    """Emit a structured warning with the required fields."""

    logger.warning(
        event,
        source="serp",
        company=company,
        error=error,
        timestamp=_utc_timestamp(),
    )


def _normalized_url(url: str) -> str:
    """Strip URL fragments so dedup works across repeated search results."""

    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def _company_name_lookup() -> dict[tuple[str, str], str]:
    """Load the configured company names keyed by ATS and slug."""

    mapping: dict[tuple[str, str], str] = {}
    for company in load_company_ats_map().companies:
        if company.slug != "TODO":
            mapping[(company.ats, company.slug.lower())] = company.name
    return mapping


def _verified_discovered_slug_count() -> int:
    """Count discovered ATS rows that are already verified in the YAML map."""

    return sum(
        1
        for company in load_company_ats_map().companies
        if company.discovered is True and company.verified is True
    )


def _planned_serp_queries() -> tuple[str, ...]:
    """Select the SERP query budget based on verified discovered ATS coverage."""

    slug_count = _verified_discovered_slug_count()
    if slug_count >= 1000:
        queries = SERP_MINIMAL_QUERIES
    elif slug_count >= 500:
        queries = SERP_TARGETED_QUERIES
    else:
        queries = SERP_QUERIES

    logger.info("serp_budget", slug_count=slug_count, queries_planned=len(queries))
    return queries


def _lever_slug(url: str) -> str | None:
    """Extract a Lever company slug from a search result URL."""

    parts = urlsplit(url)
    if parts.netloc.lower() != "jobs.lever.co":
        return None

    segments = [segment for segment in parts.path.split("/") if segment]
    if len(segments) < 2:
        return None
    return segments[0]


def _greenhouse_slug(url: str) -> str | None:
    """Extract a Greenhouse company slug from a search result URL."""

    parts = urlsplit(url)
    host = parts.netloc.lower()
    if host not in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        return None

    segments = [segment for segment in parts.path.split("/") if segment]
    if len(segments) < 3:
        return None
    if segments[1] != "jobs":
        return None
    return segments[0]


def _ashby_slug(url: str) -> str | None:
    """Extract an Ashby company slug from a search result URL."""

    parts = urlsplit(url)
    if parts.netloc.lower() != "jobs.ashbyhq.com":
        return None

    segments = [segment for segment in parts.path.split("/") if segment]
    if len(segments) < 2:
        return None
    return segments[0]


async def _run_query(client: SerpClient, query: str) -> dict[str, Any]:
    """Execute one SerpAPI query with a single 429 retry."""

    params = {
        "engine": "google",
        "q": query,
        "num": 10,
        "gl": "us",
        "hl": "en",
    }

    for attempt in range(2):
        try:
            result = await asyncio.to_thread(client.search, params)
            return dict(result)
        except SerpHTTPError as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code == 429 and attempt == 0:
                _log_warning("serp_rate_limited", company=query, error="429 Too Many Requests")
                await asyncio.sleep(60)
                continue
            raise

    return {}


def _extract_result_urls(payload: dict[str, Any]) -> list[str]:
    """Read organic search result URLs from a SerpAPI payload."""

    urls: list[str] = []
    rows = payload.get("organic_results")
    if not isinstance(rows, list):
        return urls

    for row in rows:
        if not isinstance(row, dict):
            continue
        url = row.get("link") or row.get("url")
        if isinstance(url, str) and url:
            urls.append(_normalized_url(url))
    return urls


async def scrape_serp(api_key: str) -> tuple[list[Job], list[str]]:
    """Discover additional board URLs via SerpAPI."""

    if not api_key:
        _log_warning("serp_api_key_missing", company="SERP_API_KEY", error="SERP_API_KEY not set")
        return ([], [])

    client = SerpClient(api_key=api_key)
    company_names = _company_name_lookup()
    planned_queries = _planned_serp_queries()
    discovered_companies: dict[tuple[str, str], dict[str, Any]] = {}
    fallback_urls: list[str] = []
    seen_fallback_urls: set[str] = set()

    for index, query in enumerate(planned_queries):
        try:
            payload = await _run_query(client, query)
        except Exception as exc:  # pragma: no cover - network failure path
            _log_warning("serp_query_failed", company=query, error=str(exc))
            payload = {}

        for url in _extract_result_urls(payload):
            lever_slug = _lever_slug(url)
            if lever_slug is not None:
                key = ("lever", lever_slug.lower())
                discovered_companies.setdefault(
                    key,
                    {
                        "name": company_names.get(key, lever_slug),
                        "ats": "lever",
                        "slug": lever_slug,
                        "tier": 99,
                        "discovered": True,
                        "verified": True,
                    },
                )
                continue

            greenhouse_slug = _greenhouse_slug(url)
            if greenhouse_slug is not None:
                key = ("greenhouse", greenhouse_slug.lower())
                discovered_companies.setdefault(
                    key,
                    {
                        "name": company_names.get(key, greenhouse_slug),
                        "ats": "greenhouse",
                        "slug": greenhouse_slug,
                        "tier": 99,
                        "discovered": True,
                        "verified": True,
                    },
                )
                continue

            ashby_slug = _ashby_slug(url)
            if ashby_slug is not None:
                key = ("ashby", ashby_slug.lower())
                discovered_companies.setdefault(
                    key,
                    {
                        "name": company_names.get(key, ashby_slug),
                        "ats": "ashby",
                        "slug": ashby_slug,
                        "tier": 99,
                        "discovered": True,
                        "verified": True,
                    },
                )
                continue

            if url not in seen_fallback_urls:
                seen_fallback_urls.add(url)
                fallback_urls.append(url)

        if index < len(planned_queries) - 1:
            await asyncio.sleep(1)

    appended_companies = append_company_ats_entries(list(discovered_companies.values()))
    for company in appended_companies:
        logger.info("new_company_discovered", slug=company["slug"], ats=company["ats"])

    jobs = await scrape_ats(list(discovered_companies.values()))
    return (jobs, fallback_urls)
