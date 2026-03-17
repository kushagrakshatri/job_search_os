"""One-off read-only inspection script for current job data."""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import desc, func, select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from jobsearch.db import get_db
from jobsearch.models import Job

KNOCKOUT_TERMS = (
    "sponsorship",
    "authorize",
    "citizen",
    "clearance",
    "export control",
    "itar",
    "without employer",
)

YOE_TERMS = (
    "0-1 year",
    "1-2 year",
    "2-3 year",
    "3-5 year",
    "5+ year",
    "new grad",
    "entry level",
    "recent graduate",
)


def print_title_distribution() -> None:
    """Print the top title distribution from the jobs table."""

    print("1. TITLE DISTRIBUTION")
    with get_db() as session:
        rows = session.execute(
            select(Job.title, func.count(Job.id).label("job_count"))
            .group_by(Job.title)
            .order_by(desc("job_count"), Job.title.asc())
            .limit(30)
        ).all()

    for title, count in rows:
        print(f"{count:>4}  {title}")
    if not rows:
        print("No rows found.")
    print()


def print_source_distribution() -> None:
    """Print the source distribution from the jobs table."""

    print("2. SOURCE DISTRIBUTION")
    with get_db() as session:
        rows = session.execute(
            select(Job.source, func.count(Job.id).label("job_count"))
            .group_by(Job.source)
            .order_by(desc("job_count"), Job.source.asc())
        ).all()

    for source, count in rows:
        print(f"{count:>4}  {source}")
    if not rows:
        print("No rows found.")
    print()


def print_jd_text_quality() -> None:
    """Print a random sample of job descriptions for manual inspection."""

    print("3. JD TEXT QUALITY")
    with get_db() as session:
        rows = session.execute(
            select(Job.title, Job.company, Job.source, Job.jd_text)
            .order_by(func.random())
            .limit(5)
        ).all()

    if not rows:
        print("No rows found.")
        print()
        return

    for index, (title, company, source, jd_text) in enumerate(rows, start=1):
        snippet = (jd_text or "")[:300]
        print(f"Sample {index}")
        print(f"title: {title}")
        print(f"company: {company}")
        print(f"source: {source}")
        print(f"jd_text_length: {len(jd_text or '')}")
        print(f"jd_text_preview: {snippet}")
        print()


def iter_signal_hits(terms: tuple[str, ...], limit: int) -> list[tuple[str, str, str, str]]:
    """Return up to `limit` text hits for the given search terms."""

    hits: list[tuple[str, str, str, str]] = []

    with get_db() as session:
        jobs = session.execute(
            select(Job.title, Job.company, Job.jd_text).order_by(Job.scraped_at.desc())
        ).all()

    for title, company, jd_text in jobs:
        lines = [line.strip() for line in (jd_text or "").splitlines() if line.strip()]
        if not lines and jd_text:
            lines = [jd_text.strip()]

        for line in lines:
            lowered_line = line.lower()
            for term in terms:
                if term in lowered_line:
                    hits.append((title, company, term, line))
                    if len(hits) >= limit:
                        return hits

    return hits


def print_knockout_signal_sample() -> None:
    """Print a sample of knockout-related text matches."""

    print("4. KNOCKOUT SIGNAL SAMPLE")
    hits = iter_signal_hits(KNOCKOUT_TERMS, limit=20)

    if not hits:
        print("No knockout signals found in sample.")
        print()
        return

    for title, company, term, line in hits:
        print(f"{title} | {company}")
        print(f"matched: {term}")
        print(line)
        print()


def print_yoe_signal_sample() -> None:
    """Print a sample of years-of-experience text matches."""

    print("5. YOE SIGNAL SAMPLE")
    hits = iter_signal_hits(YOE_TERMS, limit=20)

    if not hits:
        print("No YOE signals found in sample.")
        print()
        return

    for title, company, term, line in hits:
        print(f"{title} | {company}")
        print(f"matched: {term}")
        print(line)
        print()


def main() -> None:
    """Run all read-only inspection sections."""

    print_title_distribution()
    print_source_distribution()
    print_jd_text_quality()
    print_knockout_signal_sample()
    print_yoe_signal_sample()


if __name__ == "__main__":
    main()
