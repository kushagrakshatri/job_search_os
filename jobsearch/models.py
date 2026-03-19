"""ORM models for persisted job, feedback, and pipeline records."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for ORM models."""


def _utcnow() -> datetime:
    """Return the current UTC time as a naive datetime."""

    from datetime import timezone

    return datetime.now(timezone.utc).replace(tzinfo=None)


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
    embedding_computed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class JobFeedback(Base):
    """Persisted user and system feedback for job ranking quality."""

    __tablename__ = "job_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("jobs.id"),
        nullable=False,
        index=True,
    )
    signal_type: Mapped[str] = mapped_column(String, nullable=False)
    label: Mapped[int] = mapped_column(Integer, nullable=False)
    rank_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utcnow,
        index=True,
    )


class PipelineRole(Base):
    """Tracked role moving through the post-discovery application pipeline."""

    __tablename__ = "pipeline_roles"

    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    job_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    company: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False, default="discovered", index=True)
    danger_state: Mapped[str | None] = mapped_column(String, nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    contacts: Mapped[list["Contact"]] = relationship(
        back_populates="pipeline_role",
        cascade="all, delete-orphan",
        order_by="Contact.created_at",
        lazy="selectin",
    )
    outreach_log: Mapped[list["OutreachLog"]] = relationship(
        back_populates="pipeline_role",
        cascade="all, delete-orphan",
        order_by="OutreachLog.sent_at",
        lazy="selectin",
    )


class Contact(Base):
    """Contact associated with a tracked pipeline role."""

    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pipeline_role_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("pipeline_roles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str | None] = mapped_column(String, nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    pipeline_role: Mapped[PipelineRole] = relationship(back_populates="contacts")


class OutreachLog(Base):
    """One inbound or outbound outreach event tied to a pipeline role."""

    __tablename__ = "outreach_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pipeline_role_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("pipeline_roles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    contact_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("contacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    direction: Mapped[str] = mapped_column(String, nullable=False)
    channel: Mapped[str] = mapped_column(String, nullable=False)
    message_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, index=True)

    pipeline_role: Mapped[PipelineRole] = relationship(back_populates="outreach_log")
    contact: Mapped[Contact | None] = relationship()
