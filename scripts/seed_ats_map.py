"""Seed the ATS company map from public datasets and ATS URL lists."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from time import monotonic
from typing import Final
from urllib.parse import urlsplit

import aiohttp
from ruamel.yaml.comments import CommentedMap

from jobsearch.config import (
    build_company_ats_entry,
    load_company_ats_map_document,
    save_company_ats_map_document,
)

LEVER_JSON_SOURCES: Final[tuple[str, ...]] = (
    "https://github.com/nealrs/lectern/blob/master/companies.json",
    "https://raw.githubusercontent.com/nicholasgriffen/job-boards/master/lever.json",
)
GREENHOUSE_JSON_SOURCES: Final[tuple[str, ...]] = (
    "https://raw.githubusercontent.com/nicholasgriffen/job-boards/master/greenhouse.json",
    "https://raw.githubusercontent.com/tramcar/awesome-job-boards/master/data/greenhouse.json",
)
MARKDOWN_FALLBACK_SOURCES: Final[tuple[str, ...]] = (
    "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/README.md",
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/dev/README.md",
    "https://raw.githubusercontent.com/avinash201199/Paid-Internship-List/master/README.md",
)
REQUEST_TIMEOUT: Final = aiohttp.ClientTimeout(total=20)
USER_AGENT: Final = "Mozilla/5.0 JobSearchOS"


@dataclass(slots=True)
class PendingEntry:
    """Track one appended YAML row while it is being verified."""

    ats: str
    slug: str
    node: CommentedMap


class AsyncRateGate:
    """Permit requests at a fixed maximum rate across concurrent tasks."""

    def __init__(self, *, requests_per_second: float) -> None:
        self._interval = 1.0 / requests_per_second
        self._lock = asyncio.Lock()
        self._next_allowed_at = 0.0

    async def wait(self) -> None:
        """Pause until the next request slot is available."""

        async with self._lock:
            now = monotonic()
            delay = self._next_allowed_at - now
            if delay > 0:
                await asyncio.sleep(delay)
                now = monotonic()
            self._next_allowed_at = max(now, self._next_allowed_at) + self._interval


def _candidate_urls(url: str) -> list[str]:
    """Return concrete download URLs for a source, including raw GitHub fallback."""

    parts = urlsplit(url)
    if parts.netloc == "github.com" and "/blob/" in parts.path:
        raw_path = parts.path.replace("/blob/", "/", 1)
        return [f"https://raw.githubusercontent.com{raw_path}", url]
    return [url]


async def _fetch_text(session: aiohttp.ClientSession, url: str) -> str | None:
    """Fetch a text payload from the first reachable URL candidate."""

    for candidate_url in _candidate_urls(url):
        try:
            async with session.get(candidate_url) as response:
                if response.status != 200:
                    continue
                return await response.text()
        except aiohttp.ClientError:
            continue
    return None


async def _fetch_json_slugs(
    session: aiohttp.ClientSession,
    *,
    label: str,
    urls: tuple[str, ...],
) -> list[str]:
    """Fetch the first live JSON list source for an ATS."""

    for url in urls:
        payload = await _fetch_text(session, url)
        if payload is None:
            continue
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if not isinstance(decoded, list):
            continue
        slugs = [str(item).strip() for item in decoded if isinstance(item, str) and str(item).strip()]
        if slugs:
            return slugs

    print(f"{label} seed source unavailable")
    return []


def _extract_markdown_ats_slugs(markdown_text: str) -> tuple[list[str], list[str]]:
    """Extract Lever and Greenhouse board slugs from public markdown URLs."""

    lever_slugs: list[str] = []
    greenhouse_slugs: list[str] = []
    seen_lever: set[str] = set()
    seen_greenhouse: set[str] = set()

    for raw_url in re.findall(r"https://[^\s)><\"]+", markdown_text):
        parts = urlsplit(raw_url.rstrip('",'))
        host = parts.netloc.lower()
        segments = [segment for segment in parts.path.split("/") if segment]

        if host == "jobs.lever.co" and len(segments) >= 2:
            slug = segments[0].strip()
            slug_key = slug.lower()
            if slug and slug_key not in seen_lever:
                seen_lever.add(slug_key)
                lever_slugs.append(slug)
            continue

        if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"} and len(segments) >= 3:
            if segments[1] != "jobs":
                continue
            slug = segments[0].strip()
            slug_key = slug.lower()
            if slug and slug_key not in seen_greenhouse:
                seen_greenhouse.add(slug_key)
                greenhouse_slugs.append(slug)

    return (lever_slugs, greenhouse_slugs)


async def _fetch_markdown_fallback_slugs(
    session: aiohttp.ClientSession,
) -> tuple[list[str], list[str]]:
    """Fetch ATS slugs from approved public markdown job lists."""

    lever_slugs: list[str] = []
    greenhouse_slugs: list[str] = []
    seen_lever: set[str] = set()
    seen_greenhouse: set[str] = set()

    for url in MARKDOWN_FALLBACK_SOURCES:
        markdown_text = await _fetch_text(session, url)
        if markdown_text is None:
            continue

        parsed_lever, parsed_greenhouse = _extract_markdown_ats_slugs(markdown_text)
        for slug in parsed_lever:
            slug_key = slug.lower()
            if slug_key not in seen_lever:
                seen_lever.add(slug_key)
                lever_slugs.append(slug)
        for slug in parsed_greenhouse:
            slug_key = slug.lower()
            if slug_key not in seen_greenhouse:
                seen_greenhouse.add(slug_key)
                greenhouse_slugs.append(slug)

    return (lever_slugs, greenhouse_slugs)


def _append_pending_entries(
    *,
    companies: list[CommentedMap],
    existing_slugs: set[str],
    ats: str,
    slugs: list[str],
) -> list[PendingEntry]:
    """Append new ATS rows to the in-memory document and track them for verification."""

    pending_entries: list[PendingEntry] = []
    for slug in slugs:
        slug_key = slug.lower()
        if slug_key in existing_slugs:
            continue

        node = build_company_ats_entry(
            name=slug,
            ats=ats,
            slug=slug,
            tier=99,
            discovered=True,
            verified=False,
        )
        companies.append(node)
        existing_slugs.add(slug_key)
        pending_entries.append(PendingEntry(ats=ats, slug=slug, node=node))

    return pending_entries


def _verification_url(ats: str, slug: str) -> str:
    """Build the ATS verification endpoint for a slug."""

    if ats == "lever":
        return f"https://api.lever.co/v0/postings/{slug}?mode=json"
    return f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"


async def _head_status(
    session: aiohttp.ClientSession,
    *,
    url: str,
    semaphore: asyncio.Semaphore,
    rate_gate: AsyncRateGate,
) -> int | None:
    """Issue a single rate-limited HEAD request and return the HTTP status."""

    await rate_gate.wait()
    try:
        async with semaphore:
            async with session.head(url, allow_redirects=True) as response:
                return response.status
    except aiohttp.ClientError:
        return None


async def _verify_entry(
    session: aiohttp.ClientSession,
    *,
    entry: PendingEntry,
    companies: list[CommentedMap],
    semaphore: asyncio.Semaphore,
    rate_gate: AsyncRateGate,
) -> bool:
    """Verify one appended ATS slug, mutating the YAML node in place."""

    url = _verification_url(entry.ats, entry.slug)
    last_status: int | None = None

    for attempt in range(3):
        status = await _head_status(session, url=url, semaphore=semaphore, rate_gate=rate_gate)
        last_status = status

        if status == 200:
            entry.node["verified"] = True
            return False

        if status == 404:
            if entry.node in companies:
                companies.remove(entry.node)
            print(f"{entry.slug} — 404, removed")
            return True

        if attempt < 2:
            await asyncio.sleep(0.5 * (attempt + 1))

    if entry.node in companies:
        companies.remove(entry.node)
    status_text = "error" if last_status is None else str(last_status)
    print(f"{entry.slug} — verification failed ({status_text}), removed")
    return True


async def _verify_pending_entries(
    pending_entries: list[PendingEntry],
    *,
    companies: list[CommentedMap],
) -> int:
    """Verify all pending rows concurrently and return the number removed."""

    if not pending_entries:
        return 0

    semaphore = asyncio.Semaphore(10)
    rate_gate = AsyncRateGate(requests_per_second=5)
    connector = aiohttp.TCPConnector(limit=20)

    async with aiohttp.ClientSession(
        timeout=REQUEST_TIMEOUT,
        connector=connector,
        headers={"User-Agent": USER_AGENT},
    ) as session:
        tasks = [
            _verify_entry(
                session,
                entry=entry,
                companies=companies,
                semaphore=semaphore,
                rate_gate=rate_gate,
            )
            for entry in pending_entries
        ]
        results = await asyncio.gather(*tasks)

    return sum(1 for removed in results if removed)


async def _seed() -> None:
    """Run the seed flow end to end."""

    connector = aiohttp.TCPConnector(limit=10)
    async with aiohttp.ClientSession(
        timeout=REQUEST_TIMEOUT,
        connector=connector,
        headers={"User-Agent": USER_AGENT},
    ) as session:
        lever_json_slugs = await _fetch_json_slugs(session, label="Lever", urls=LEVER_JSON_SOURCES)
        greenhouse_json_slugs = await _fetch_json_slugs(
            session,
            label="Greenhouse",
            urls=GREENHOUSE_JSON_SOURCES,
        )
        fallback_lever_slugs, fallback_greenhouse_slugs = await _fetch_markdown_fallback_slugs(session)

    path, document = load_company_ats_map_document()
    companies = document["companies"]
    existing_slugs = {str(company.get("slug", "")).lower() for company in companies if isinstance(company, dict)}

    lever_pending = _append_pending_entries(
        companies=companies,
        existing_slugs=existing_slugs,
        ats="lever",
        slugs=[*lever_json_slugs, *fallback_lever_slugs],
    )
    greenhouse_pending = _append_pending_entries(
        companies=companies,
        existing_slugs=existing_slugs,
        ats="greenhouse",
        slugs=[*greenhouse_json_slugs, *fallback_greenhouse_slugs],
    )

    pending_entries = [*lever_pending, *greenhouse_pending]
    removed = await _verify_pending_entries(pending_entries, companies=companies)
    survivors = len(pending_entries) - removed

    if survivors > 0:
        save_company_ats_map_document(document, path)

    print(
        f"Seed complete: {len(lever_pending)} Lever + {len(greenhouse_pending)} Greenhouse "
        f"added, {removed} removed after 404 verification"
    )


def main() -> None:
    """Run the ATS map seed script."""

    asyncio.run(_seed())


if __name__ == "__main__":
    main()
