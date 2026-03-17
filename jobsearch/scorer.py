"""Codex-backed scoring workflow for pending jobs."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import func, select

from jobsearch.codex_client import complete, get_access_token
from jobsearch.config import get_settings
from jobsearch.db import get_db
from jobsearch.models import Job
from jobsearch.prompts import SCORER_SYSTEM_PROMPT

logger = structlog.get_logger(__name__)


def _utcnow() -> datetime:
    """Return the current UTC time as a naive datetime."""

    return datetime.now(timezone.utc).replace(tzinfo=None)


def build_user_message(job: Job) -> str:
    """Build the scorer user message for a single job."""

    return (
        f"Title: {job.title}\n"
        f"Company: {job.company}\n"
        f"Location: {job.location} | Remote: {job.is_remote}\n\n"
        f"Job description:\n{job.jd_text}"
    )


def _update_job_from_result(db_job: Job, parsed: dict[str, Any]) -> None:
    """Apply the parsed LLM result to a persisted job row."""

    scores = parsed["scores"]

    db_job.knocked_out = bool(parsed["knocked_out"])
    db_job.knockout_reason = parsed["knockout_reason"]
    db_job.score_tech_stack = scores["tech_stack"]
    db_job.score_role_fit = scores["role_fit"]
    db_job.score_work_auth = scores["work_auth"]
    db_job.score_interviewability = scores["interviewability"]
    db_job.score_ai_signal = scores["ai_signal"]
    db_job.score_growth = scores["growth"]
    db_job.total_score = parsed["total_score"]
    db_job.tier = parsed["tier"]
    db_job.score_breakdown = parsed
    db_job.llm_scored = True
    db_job.llm_scored_at = _utcnow()


def _extract_json_object_text(text: str, job_id: str) -> str | None:
    """Normalize model output and isolate the outermost JSON object."""

    cleaned = "\n".join(
        line for line in text.strip().splitlines() if not line.lstrip().startswith("```")
    ).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")

    if start == -1 or end == -1 or end < start:
        logger.warning(
            "score_job_missing_json_object",
            job_id=job_id,
            raw_response_excerpt=cleaned[:200],
        )
        return None

    return cleaned[start : end + 1]


def _mark_job_skipped_for_missing_jd(job: Job) -> None:
    """Persist a skip state for jobs with unusable descriptions."""

    with get_db() as session:
        db_job = session.get(Job, job.id)
        if db_job is None:
            logger.warning("score_job_missing_db_row", job_id=job.id)
            return

        db_job.knocked_out = True
        db_job.knockout_reason = "jd_missing"
        db_job.score_tech_stack = None
        db_job.score_role_fit = None
        db_job.score_work_auth = None
        db_job.score_interviewability = None
        db_job.score_ai_signal = None
        db_job.score_growth = None
        db_job.total_score = None
        db_job.tier = "skip"
        db_job.score_breakdown = None
        db_job.llm_scored = True
        db_job.llm_scored_at = _utcnow()
        session.commit()

    logger.warning(
        "skipped_jd_missing",
        job_id=job.id,
        title=job.title,
        company=job.company,
    )


def score_job(job: Job) -> dict[str, Any] | None:
    """Score a single job via the configured Codex backend."""

    if job.jd_text is None or len(job.jd_text.strip()) < 300:
        _mark_job_skipped_for_missing_jd(job)
        return None

    settings = get_settings()
    user_message = build_user_message(job)

    try:
        raw_text = complete(
            SCORER_SYSTEM_PROMPT,
            user_message,
            model=settings.SCORER_MODEL,
        )
        json_text = _extract_json_object_text(raw_text, job.id)
        if json_text is None:
            return None
        parsed = json.loads(json_text)
    except json.JSONDecodeError as exc:
        logger.warning("score_job_failed", job_id=job.id, error=str(exc))
        return None
    except RuntimeError as exc:
        logger.warning("score_job_failed", job_id=job.id, error=str(exc))
        return None

    try:
        with get_db() as session:
            db_job = session.get(Job, job.id)
            if db_job is None:
                logger.warning("score_job_missing_db_row", job_id=job.id)
                return None

            _update_job_from_result(db_job, parsed)
            session.commit()
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("score_job_failed", job_id=job.id, error=str(exc))
        return None

    return parsed


def score_pending(limit: int = 50) -> tuple[int, int]:
    """Score recent jobs that have not yet been processed by the LLM."""

    settings = get_settings()
    now = _utcnow()
    seven_days_ago = now - timedelta(days=7)
    today_midnight_utc = now.replace(hour=0, minute=0, second=0, microsecond=0)

    with get_db() as session:
        scored_today = session.scalar(
            select(func.count(Job.id)).where(Job.llm_scored_at >= today_midnight_utc)
        ) or 0

        if scored_today >= settings.DAILY_SCORE_LIMIT:
            logger.warning(
                "daily_score_limit_reached",
                daily_score_limit=settings.DAILY_SCORE_LIMIT,
                scored_today=scored_today,
            )
            return (0, 0)

        remaining_budget = settings.DAILY_SCORE_LIMIT - scored_today
        effective_limit = min(limit, remaining_budget)

        pending_jobs = list(
            session.scalars(
                select(Job)
                .where(Job.llm_scored.is_(False), Job.scraped_at > seven_days_ago)
                .order_by(Job.scraped_at.asc(), Job.id.asc())
                .limit(effective_limit)
            )
        )

    success_count = 0
    failure_count = 0

    for job in pending_jobs:
        result = score_job(job)
        if result is None:
            failure_count += 1
        else:
            success_count += 1

    return (success_count, failure_count)


def main() -> None:
    """Score pending seed jobs and print their score summaries."""

    from scripts.seed import SEED_JOBS

    try:
        get_access_token()
    except RuntimeError as exc:
        print(
            str(exc),
            file=sys.stderr,
        )
        raise SystemExit(1)

    score_pending(limit=10)

    seed_ids_in_order = [str(row["id"]) for row in SEED_JOBS]

    with get_db() as session:
        jobs_by_id = {
            job.id: job
            for job in session.scalars(select(Job).where(Job.id.in_(seed_ids_in_order)))
        }

    for seed_id in seed_ids_in_order:
        job = jobs_by_id[seed_id]
        print(f"{job.title} | {job.tier} | {job.total_score}")


if __name__ == "__main__":
    main()
