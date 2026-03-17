"""Audit scored jobs with tabular output and optional re-scoring."""

from __future__ import annotations

import argparse

from sqlalchemy import select

from jobsearch.db import get_db
from jobsearch.models import Job
from jobsearch.scorer import score_pending

DEFAULT_LIMIT = 20
TITLE_WIDTH = 30
COMPANY_WIDTH = 24
RANK_WIDTH = 4
TIER_WIDTH = 4
TOTAL_WIDTH = 5
SCORE_WIDTH = 4
GROWTH_WIDTH = 6
KO_WIDTH = 2

RATIONAL_LABELS: tuple[tuple[str, str], ...] = (
    ("tech_stack", "tech_stack"),
    ("role_fit", "role_fit"),
    ("work_auth", "work_auth"),
    ("interviewability", "interviewability"),
    ("ai_signal", "ai_signal"),
    ("growth", "growth"),
)


def _build_parser() -> argparse.ArgumentParser:
    """Create the CLI argument parser."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Maximum number of scored jobs to print (default: {DEFAULT_LIMIT}).",
    )
    parser.add_argument(
        "--tier",
        choices=("A", "B", "C", "skip"),
        help="Filter by tier before applying the limit.",
    )
    parser.add_argument(
        "--rescore",
        action="store_true",
        help="Reset and re-score the jobs that would be printed before showing them.",
    )
    return parser


def _truncate(value: str, width: int) -> str:
    """Truncate text for fixed-width cells."""

    if len(value) <= width:
        return value
    if width <= 3:
        return value[:width]
    return f"{value[: width - 3]}..."


def _format_cell(value: object, width: int, *, align: str = "<") -> str:
    """Format a table cell to a fixed width."""

    text = "" if value is None else str(value)
    if align == "<":
        text = _truncate(text, width)
    elif len(text) > width:
        text = text[-width:]
    return f"{text:{align}{width}}"


def _query_jobs(limit: int, tier: str | None = None) -> list[Job]:
    """Load scored jobs ordered by total score descending."""

    statement = (
        select(Job)
        .where(Job.llm_scored.is_(True), Job.total_score.is_not(None))
        .order_by(Job.total_score.desc(), Job.title.asc(), Job.id.asc())
    )
    if tier is not None:
        statement = statement.where(Job.tier == tier)

    with get_db() as session:
        return list(session.scalars(statement.limit(limit)))


def _reset_jobs_for_rescore(job_ids: list[str]) -> None:
    """Reset scoring fields for the selected jobs."""

    if not job_ids:
        return

    with get_db() as session:
        jobs = list(session.scalars(select(Job).where(Job.id.in_(job_ids))))
        for job in jobs:
            job.knocked_out = False
            job.knockout_reason = None
            job.score_tech_stack = None
            job.score_role_fit = None
            job.score_work_auth = None
            job.score_interviewability = None
            job.score_ai_signal = None
            job.score_growth = None
            job.total_score = None
            job.tier = None
            job.score_breakdown = None
            job.llm_scored = False
            job.llm_scored_at = None
        session.commit()


def _reload_jobs(job_ids: list[str]) -> list[Job]:
    """Reload jobs in the original order."""

    if not job_ids:
        return []

    with get_db() as session:
        jobs_by_id = {
            job.id: job
            for job in session.scalars(select(Job).where(Job.id.in_(job_ids)))
        }
    return [jobs_by_id[job_id] for job_id in job_ids if job_id in jobs_by_id]


def _extract_rationales(job: Job) -> dict[str, str] | None:
    """Return the rationale mapping from a scored job."""

    breakdown = job.score_breakdown
    if not isinstance(breakdown, dict):
        return None

    rationale = breakdown.get("rationale")
    if not isinstance(rationale, dict):
        return None

    result: dict[str, str] = {}
    for key, _ in RATIONAL_LABELS:
        value = rationale.get(key)
        if not isinstance(value, str):
            return None
        result[key] = value
    return result


def _print_table(jobs: list[Job]) -> None:
    """Print a readable ASCII table plus rationale lines."""

    header = " | ".join(
        (
            _format_cell("Rank", RANK_WIDTH),
            _format_cell("Title", TITLE_WIDTH),
            _format_cell("Company", COMPANY_WIDTH),
            _format_cell("Tier", TIER_WIDTH),
            _format_cell("Total", TOTAL_WIDTH, align=">"),
            _format_cell("Tech", SCORE_WIDTH, align=">"),
            _format_cell("Role", SCORE_WIDTH, align=">"),
            _format_cell("Auth", SCORE_WIDTH, align=">"),
            _format_cell("Intv", SCORE_WIDTH, align=">"),
            _format_cell("AI", SCORE_WIDTH, align=">"),
            _format_cell("Growth", GROWTH_WIDTH, align=">"),
            _format_cell("KO", KO_WIDTH, align=">"),
        )
    )
    print(header)
    print("-" * len(header))

    for index, job in enumerate(jobs, start=1):
        print(
            " | ".join(
                (
                    _format_cell(index, RANK_WIDTH),
                    _format_cell(job.title, TITLE_WIDTH),
                    _format_cell(job.company, COMPANY_WIDTH),
                    _format_cell(job.tier or "", TIER_WIDTH),
                    _format_cell(job.total_score, TOTAL_WIDTH, align=">"),
                    _format_cell(job.score_tech_stack, SCORE_WIDTH, align=">"),
                    _format_cell(job.score_role_fit, SCORE_WIDTH, align=">"),
                    _format_cell(job.score_work_auth, SCORE_WIDTH, align=">"),
                    _format_cell(job.score_interviewability, SCORE_WIDTH, align=">"),
                    _format_cell(job.score_ai_signal, SCORE_WIDTH, align=">"),
                    _format_cell(job.score_growth, GROWTH_WIDTH, align=">"),
                    _format_cell("Y" if job.knocked_out else "N", KO_WIDTH, align=">"),
                )
            )
        )

        rationales = _extract_rationales(job)
        if rationales is None:
            print("    (not scored)")
            continue

        for key, label in RATIONAL_LABELS:
            print(f"    {label}: {rationales[key]}")


def audit_scores(
    limit: int | None = None,
    tier: str | None = None,
    *,
    rescore: bool = False,
) -> list[Job]:
    """Load scored jobs for auditing, optionally re-scoring them first."""

    effective_limit = DEFAULT_LIMIT if limit is None else limit
    jobs = _query_jobs(effective_limit, tier=tier)

    if rescore and jobs:
        job_ids = [job.id for job in jobs]
        _reset_jobs_for_rescore(job_ids)
        score_pending(limit=len(job_ids))
        jobs = _reload_jobs(job_ids)

    return jobs


def main() -> None:
    """Run the score audit script."""

    args = _build_parser().parse_args()
    jobs = audit_scores(args.limit, args.tier, rescore=args.rescore)
    _print_table(jobs)


if __name__ == "__main__":
    main()
