"""Pipeline role state machine and persistence helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from jobsearch.db import get_db
from jobsearch.models import Contact, Job, OutreachLog, PipelineRole

VALID_TRANSITIONS: Final[dict[str, list[str]]] = {
    "discovered": ["applied"],
    "applied": ["human_touched", "closed"],
    "human_touched": ["screen", "closed"],
    "screen": ["loop", "closed"],
    "loop": ["closed"],
    "closed": [],
}

VALID_DIRECTIONS: Final[set[str]] = {"sent", "received"}
VALID_CHANNELS: Final[set[str]] = {"linkedin", "email", "telegram", "other"}

STALE_APPLIED: Final = "STALE_APPLIED"
FOLLOW_UP_IGNORED: Final = "FOLLOW_UP_IGNORED"
OLD_POSTING: Final = "OLD_POSTING"
GHOSTED_SCREEN: Final = "GHOSTED_SCREEN"


def _utcnow() -> datetime:
    """Return the current UTC time as a naive datetime."""

    return datetime.now(timezone.utc).replace(tzinfo=None)


def create_pipeline_role(
    job_id: str | None,
    company: str,
    title: str,
    url: str,
) -> PipelineRole:
    """Create a new pipeline role or return the existing row for a linked job."""

    with get_db() as session:
        if job_id is not None:
            existing = session.scalar(
                select(PipelineRole).where(PipelineRole.job_id == job_id)
            )
            if existing is not None:
                return existing

        role = PipelineRole(
            job_id=job_id,
            company=company,
            title=title,
            url=url,
            state="discovered",
        )
        session.add(role)
        session.commit()
        return role


def advance_state(
    role_id: str,
    new_state: str,
    closed_reason: str | None = None,
) -> PipelineRole:
    """Advance one pipeline role to the next valid state."""

    normalized_state = new_state.strip().lower()
    if normalized_state not in VALID_TRANSITIONS:
        raise ValueError(f"Invalid state '{new_state}'.")

    with get_db() as session:
        role = session.get(PipelineRole, role_id)
        if role is None:
            raise ValueError(f"Pipeline role '{role_id}' not found.")

        if role.state == normalized_state:
            raise ValueError(
                f"Role '{role_id}' is already in state '{normalized_state}'."
            )

        allowed = VALID_TRANSITIONS.get(role.state, [])
        if normalized_state not in allowed:
            raise ValueError(
                f"Invalid transition from '{role.state}' to '{normalized_state}'. "
                f"Allowed: {', '.join(allowed) or 'none'}."
            )

        now = _utcnow()
        role.state = normalized_state
        role.last_activity_at = now
        role.danger_state = None
        if normalized_state == "applied" and role.applied_at is None:
            role.applied_at = now
        if normalized_state == "closed":
            role.closed_reason = closed_reason

        session.commit()
        return role


def add_contact(
    role_id: str,
    name: str,
    role: str | None,
    linkedin_url: str | None,
    email: str | None,
    notes: str | None,
) -> Contact:
    """Create a contact linked to a pipeline role."""

    with get_db() as session:
        pipeline_role = session.get(PipelineRole, role_id)
        if pipeline_role is None:
            raise ValueError(f"Pipeline role '{role_id}' not found.")

        contact = Contact(
            pipeline_role_id=role_id,
            name=name,
            role=role,
            linkedin_url=linkedin_url,
            email=email,
            notes=notes,
        )
        session.add(contact)
        session.commit()
        return contact


def log_outreach(
    role_id: str,
    direction: str,
    channel: str,
    message_summary: str | None,
    contact_id: int | None,
) -> OutreachLog:
    """Record one outreach interaction and bump role activity."""

    normalized_direction = direction.strip().lower()
    normalized_channel = channel.strip().lower()

    if normalized_direction not in VALID_DIRECTIONS:
        raise ValueError(f"Invalid direction '{direction}'. Use sent or received.")
    if normalized_channel not in VALID_CHANNELS:
        raise ValueError(
            f"Invalid channel '{channel}'. Use linkedin, email, telegram, or other."
        )

    with get_db() as session:
        pipeline_role = session.get(PipelineRole, role_id)
        if pipeline_role is None:
            raise ValueError(f"Pipeline role '{role_id}' not found.")

        if contact_id is not None:
            contact = session.get(Contact, contact_id)
            if contact is None:
                raise ValueError(f"Contact '{contact_id}' not found.")
            if contact.pipeline_role_id != role_id:
                raise ValueError(
                    f"Contact '{contact_id}' is not linked to pipeline role '{role_id}'."
                )

        log_entry = OutreachLog(
            pipeline_role_id=role_id,
            contact_id=contact_id,
            direction=normalized_direction,
            channel=normalized_channel,
            message_summary=message_summary,
        )
        pipeline_role.last_activity_at = _utcnow()
        session.add(log_entry)
        session.commit()
        return log_entry


def check_danger_states() -> list[PipelineRole]:
    """Refresh all derived danger states for non-closed roles."""

    now = _utcnow()
    stale_applied_cutoff = now - timedelta(hours=72)
    old_posting_cutoff = now - timedelta(days=21)
    ghosted_screen_cutoff = now - timedelta(days=5)

    with get_db() as session:
        roles = list(
            session.scalars(
                select(PipelineRole)
                .where(PipelineRole.state != "closed")
                .options(
                    selectinload(PipelineRole.contacts),
                    selectinload(PipelineRole.outreach_log),
                )
            )
        )

        job_ids = [role.job_id for role in roles if role.job_id]
        jobs_by_id = {
            job.id: job
            for job in session.scalars(select(Job).where(Job.id.in_(job_ids)))
        } if job_ids else {}

        flagged_roles: list[PipelineRole] = []
        for role in roles:
            received_count = sum(
                1 for entry in role.outreach_log if entry.direction == "received"
            )
            sent_count = sum(
                1 for entry in role.outreach_log if entry.direction == "sent"
            )
            linked_job = jobs_by_id.get(role.job_id) if role.job_id else None

            danger_state: str | None = None
            if (
                role.state == "applied"
                and role.applied_at is not None
                and role.applied_at < stale_applied_cutoff
                and received_count == 0
            ):
                danger_state = STALE_APPLIED
            elif sent_count >= 2 and received_count == 0:
                danger_state = FOLLOW_UP_IGNORED
            elif (
                role.state == "screen"
                and role.last_activity_at is not None
                and role.last_activity_at < ghosted_screen_cutoff
            ):
                danger_state = GHOSTED_SCREEN
            elif (
                linked_job is not None
                and linked_job.scraped_at < old_posting_cutoff
            ):
                danger_state = OLD_POSTING

            role.danger_state = danger_state
            if danger_state is not None:
                flagged_roles.append(role)

        session.commit()
        return flagged_roles


def get_active_roles() -> list[PipelineRole]:
    """Return all open pipeline roles with contacts and outreach preloaded."""

    with get_db() as session:
        return list(
            session.scalars(
                select(PipelineRole)
                .where(PipelineRole.state != "closed")
                .order_by(PipelineRole.created_at.desc(), PipelineRole.id.asc())
                .options(
                    selectinload(PipelineRole.contacts),
                    selectinload(PipelineRole.outreach_log),
                )
            )
        )


def get_role_summary(role_id: str) -> dict:
    """Return one pipeline role plus contact, outreach, and linked-job context."""

    with get_db() as session:
        role = session.scalar(
            select(PipelineRole)
            .where(PipelineRole.id == role_id)
            .options(
                selectinload(PipelineRole.contacts),
                selectinload(PipelineRole.outreach_log),
            )
        )
        if role is None:
            raise ValueError(f"Pipeline role '{role_id}' not found.")

        linked_job = session.get(Job, role.job_id) if role.job_id else None
        return {
            "id": role.id,
            "job_id": role.job_id,
            "company": role.company,
            "title": role.title,
            "url": role.url,
            "state": role.state,
            "danger_state": role.danger_state,
            "applied_at": role.applied_at,
            "last_activity_at": role.last_activity_at,
            "closed_reason": role.closed_reason,
            "notes": role.notes,
            "created_at": role.created_at,
            "updated_at": role.updated_at,
            "contacts": [
                {
                    "id": contact.id,
                    "name": contact.name,
                    "role": contact.role,
                    "linkedin_url": contact.linkedin_url,
                    "email": contact.email,
                    "notes": contact.notes,
                    "created_at": contact.created_at,
                }
                for contact in role.contacts
            ],
            "outreach_log": [
                {
                    "id": entry.id,
                    "contact_id": entry.contact_id,
                    "direction": entry.direction,
                    "channel": entry.channel,
                    "message_summary": entry.message_summary,
                    "sent_at": entry.sent_at,
                }
                for entry in role.outreach_log
            ],
            "linked_job": (
                {
                    "id": linked_job.id,
                    "title": linked_job.title,
                    "company": linked_job.company,
                }
                if linked_job is not None
                else None
            ),
        }
