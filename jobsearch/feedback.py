"""Feedback logging and implicit rejection tracking."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select

from jobsearch.db import get_db
from jobsearch.models import Job, JobFeedback

logger = structlog.get_logger(__name__)


def _utcnow() -> datetime:
    """Return the current UTC time as a naive datetime."""

    return datetime.now(timezone.utc).replace(tzinfo=None)


def log_feedback(
    job_id: str,
    label: int,
    signal_type: str,
    rank_position: int | None = None,
    embedding_score: float | None = None,
    source: str = "telegram",
) -> None:
    """Insert one feedback row for a job interaction."""

    with get_db() as session:
        session.add(
            JobFeedback(
                job_id=job_id,
                signal_type=signal_type,
                label=label,
                rank_position=rank_position,
                embedding_score=embedding_score,
                source=source,
            )
        )
        session.commit()

    logger.info(
        "feedback_logged",
        job_id=job_id,
        label=label,
        signal=signal_type,
    )


def mark_implicit_rejects() -> int:
    """Insert label-0 feedback rows for stale alerts with no feedback yet."""

    cutoff = _utcnow() - timedelta(hours=72)
    feedback_exists = select(JobFeedback.id).where(JobFeedback.job_id == Job.id).exists()

    with get_db() as session:
        stale_job_ids = list(
            session.scalars(
                select(Job.id).where(
                    Job.alerted_at.is_not(None),
                    Job.alerted_at < cutoff,
                    ~feedback_exists,
                )
            )
        )

        for job_id in stale_job_ids:
            session.add(
                JobFeedback(
                    job_id=job_id,
                    signal_type="implicit_reject",
                    label=0,
                    source="scheduler",
                )
            )

        session.commit()

    if stale_job_ids:
        logger.info("implicit_rejects_marked", count=len(stale_job_ids))
    return len(stale_job_ids)
