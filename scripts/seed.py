"""Seed script for inserting local development jobs."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from jobsearch.db import get_db
from jobsearch.models import Job


def build_score_breakdown(
    *,
    knocked_out: bool,
    knockout_reason: str | None,
    tech_stack: int,
    role_fit: int,
    work_auth: int,
    interviewability: int,
    ai_signal: int,
    growth: int,
    total_score: int,
    tier: str,
) -> dict[str, object]:
    """Build a realistic score payload for a seeded job."""

    return {
        "knocked_out": knocked_out,
        "knockout_reason": knockout_reason,
        "scores": {
            "tech_stack": tech_stack,
            "role_fit": role_fit,
            "work_auth": work_auth,
            "interviewability": interviewability,
            "ai_signal": ai_signal,
            "growth": growth,
        },
        "rationale": {
            "tech_stack": "Strong overlap with Python, agent workflows, and ML platform tooling.",
            "role_fit": "The title is aligned with AI and applied ML engineering work.",
            "work_auth": "Employer size and role profile suggest a reasonable work authorization path.",
            "interviewability": "Experience asks look compatible with a graduate-level candidate profile.",
            "ai_signal": "The team appears to ship AI features or infrastructure directly.",
            "growth": "The role offers credible portfolio and technical upside.",
        },
        "total_score": total_score,
        "tier": tier,
    }


SEED_JOBS: list[dict[str, object]] = [
    {
        "id": "6f5e3417-8c08-4c48-97f1-6f9c5b4f7341",
        "title": "AI Engineer",
        "company": "Microsoft",
        "location": "Redmond, WA",
        "is_remote": True,
        "url": "https://careers.microsoft.com/us/en/job/AI-ENGINEER-001",
        "url_hash": "urlhash000000001",
        "slug_hash": "slughash00000001",
        "source": "linkedin",
        "scraped_at": datetime(2026, 3, 16, 8, 0, 0),
        "jd_text": "Build LLM-powered copilots, retrieval workflows, and evaluation pipelines for product teams.",
        "knocked_out": False,
        "knockout_reason": None,
        "score_tech_stack": 23,
        "score_role_fit": 18,
        "score_work_auth": 17,
        "score_interviewability": 13,
        "score_ai_signal": 8,
        "score_growth": 6,
        "total_score": 85,
        "tier": "A",
        "score_breakdown": build_score_breakdown(
            knocked_out=False,
            knockout_reason=None,
            tech_stack=23,
            role_fit=18,
            work_auth=17,
            interviewability=13,
            ai_signal=8,
            growth=6,
            total_score=85,
            tier="A",
        ),
        "llm_scored": True,
        "llm_scored_at": datetime(2026, 3, 16, 8, 10, 0),
        "alerted_at": datetime(2026, 3, 16, 8, 12, 0),
    },
    {
        "id": "d9ce63a7-3147-49c8-9c2f-dbb76a3480a2",
        "title": "Applied ML Engineer",
        "company": "Salesforce",
        "location": "San Francisco, CA",
        "is_remote": False,
        "url": "https://careers.salesforce.com/en/jobs/APPLIED-ML-002",
        "url_hash": "urlhash000000002",
        "slug_hash": "slughash00000002",
        "source": "indeed",
        "scraped_at": datetime(2026, 3, 16, 8, 5, 0),
        "jd_text": "Ship production ranking and generation systems with Python services and offline evaluation tooling.",
        "knocked_out": False,
        "knockout_reason": None,
        "score_tech_stack": 18,
        "score_role_fit": 16,
        "score_work_auth": 15,
        "score_interviewability": 11,
        "score_ai_signal": 6,
        "score_growth": 6,
        "total_score": 72,
        "tier": "B",
        "score_breakdown": build_score_breakdown(
            knocked_out=False,
            knockout_reason=None,
            tech_stack=18,
            role_fit=16,
            work_auth=15,
            interviewability=11,
            ai_signal=6,
            growth=6,
            total_score=72,
            tier="B",
        ),
        "llm_scored": True,
        "llm_scored_at": datetime(2026, 3, 16, 8, 14, 0),
        "alerted_at": datetime(2026, 3, 16, 8, 16, 0),
    },
    {
        "id": "eb85a0a0-b9d4-4b17-8b30-7f2500b795d4",
        "title": "Machine Learning Engineer",
        "company": "Adobe",
        "location": "San Jose, CA",
        "is_remote": True,
        "url": "https://careers.adobe.com/us/en/job/ML-ENGINEER-003",
        "url_hash": "urlhash000000003",
        "slug_hash": "slughash00000003",
        "source": "glassdoor",
        "scraped_at": datetime(2026, 3, 16, 8, 8, 0),
        "jd_text": "Support internal ML platform APIs and experimentation tooling for content intelligence teams.",
        "knocked_out": False,
        "knockout_reason": None,
        "score_tech_stack": 15,
        "score_role_fit": 12,
        "score_work_auth": 11,
        "score_interviewability": 9,
        "score_ai_signal": 5,
        "score_growth": 6,
        "total_score": 58,
        "tier": "C",
        "score_breakdown": build_score_breakdown(
            knocked_out=False,
            knockout_reason=None,
            tech_stack=15,
            role_fit=12,
            work_auth=11,
            interviewability=9,
            ai_signal=5,
            growth=6,
            total_score=58,
            tier="C",
        ),
        "llm_scored": True,
        "llm_scored_at": datetime(2026, 3, 16, 8, 17, 0),
        "alerted_at": None,
    },
    {
        "id": "a55c19e2-bd8a-4541-bd94-db4942bc52cc",
        "title": "Software Engineer, AI Platform",
        "company": "Cisco",
        "location": "San Francisco, CA",
        "is_remote": False,
        "url": "https://jobs.cisco.com/jobs/SWE-AI-PLATFORM-004",
        "url_hash": "urlhash000000004",
        "slug_hash": "slughash00000004",
        "source": "zip_recruiter",
        "scraped_at": datetime(2026, 3, 16, 8, 11, 0),
        "jd_text": "Maintain internal Python services for model deployment, telemetry, and experimentation support.",
        "knocked_out": False,
        "knockout_reason": None,
        "score_tech_stack": 12,
        "score_role_fit": 10,
        "score_work_auth": 9,
        "score_interviewability": 7,
        "score_ai_signal": 3,
        "score_growth": 4,
        "total_score": 45,
        "tier": "C",
        "score_breakdown": build_score_breakdown(
            knocked_out=False,
            knockout_reason=None,
            tech_stack=12,
            role_fit=10,
            work_auth=9,
            interviewability=7,
            ai_signal=3,
            growth=4,
            total_score=45,
            tier="C",
        ),
        "llm_scored": True,
        "llm_scored_at": datetime(2026, 3, 16, 8, 19, 0),
        "alerted_at": None,
    },
    {
        "id": "13bc5d50-6332-43d7-99f0-150dd43ed157",
        "title": "Software Engineer II",
        "company": "Oracle",
        "location": "Austin, TX",
        "is_remote": False,
        "url": "https://careers.oracle.com/jobs/SOFTWARE-ENGINEER-005",
        "url_hash": "urlhash000000005",
        "slug_hash": "slughash00000005",
        "source": "linkedin",
        "scraped_at": datetime(2026, 3, 16, 8, 13, 0),
        "jd_text": "General backend development role with limited AI scope and higher expected autonomy.",
        "knocked_out": False,
        "knockout_reason": None,
        "score_tech_stack": 8,
        "score_role_fit": 6,
        "score_work_auth": 7,
        "score_interviewability": 4,
        "score_ai_signal": 2,
        "score_growth": 3,
        "total_score": 30,
        "tier": "skip",
        "score_breakdown": build_score_breakdown(
            knocked_out=False,
            knockout_reason=None,
            tech_stack=8,
            role_fit=6,
            work_auth=7,
            interviewability=4,
            ai_signal=2,
            growth=3,
            total_score=30,
            tier="skip",
        ),
        "llm_scored": True,
        "llm_scored_at": datetime(2026, 3, 16, 8, 21, 0),
        "alerted_at": None,
    },
]


def seed_fake_jobs(count: int = 5) -> int:
    """Insert fake job records for local development and testing."""

    if count != 5:
        raise ValueError("seed_fake_jobs only supports inserting exactly 5 jobs.")

    with get_db() as session:
        for row in SEED_JOBS[:count]:
            existing = session.scalar(select(Job).where(Job.url == row["url"]))
            if existing is None:
                session.add(Job(**row))
                continue

            for field, value in row.items():
                setattr(existing, field, value)

        session.commit()

    return count


def main() -> None:
    """Run the local development seed script."""

    seeded = seed_fake_jobs()
    print(f"Seeded {seeded} jobs")


if __name__ == "__main__":
    main()
