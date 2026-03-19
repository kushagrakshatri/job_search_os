"""Reset reranker state so existing jobs can be rescored with the current rubric."""

from __future__ import annotations

from sqlalchemy import select

from jobsearch.db import get_db
from jobsearch.models import Job


def rescore_all() -> int:
    """Reset all scored jobs without touching jobs or embeddings."""

    with get_db() as session:
        jobs = list(
            session.scalars(
                select(Job).where(
                    Job.llm_scored.is_(True),
                    Job.tier.is_not(None),
                )
            )
        )

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
            job.alerted_at = None

        session.commit()

    return len(jobs)


def main() -> None:
    """Reset scored jobs and print the affected row count."""

    reset_count = rescore_all()
    print(f"Reset {reset_count} scored jobs")


if __name__ == "__main__":
    main()
