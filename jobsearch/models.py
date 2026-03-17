"""ORM models for persisted job records."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for ORM models."""


class Job(Base):
    """Persisted job posting and scoring record."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    company: Mapped[str] = mapped_column(String, nullable=False)
    location: Mapped[str] = mapped_column(String, nullable=False)
    is_remote: Mapped[bool] = mapped_column(Boolean, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    url_hash: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    slug_hash: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    scraped_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    jd_text: Mapped[str] = mapped_column(Text, nullable=False)
    knocked_out: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    knockout_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    score_tech_stack: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_role_fit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_work_auth: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_interviewability: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_ai_signal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_growth: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tier: Mapped[str | None] = mapped_column(String, nullable=True)
    score_breakdown: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    llm_scored: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    llm_scored_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    alerted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
