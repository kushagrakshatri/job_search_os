"""Reset embedding flags in SQLite without deleting ChromaDB data."""

from __future__ import annotations

from sqlalchemy import select

from jobsearch.db import get_db
from jobsearch.models import Job


def reset_embeddings() -> int:
    """Set embedding_computed=False for all jobs in SQLite."""

    with get_db() as session:
        jobs = list(session.scalars(select(Job)))
        for job in jobs:
            job.embedding_computed = False
        session.commit()

    return len(jobs)


def main() -> None:
    """Reset embedding flags and print the affected row count."""

    reset_count = reset_embeddings()
    print(f"Reset embedding_computed=False for {reset_count} jobs")


if __name__ == "__main__":
    main()
