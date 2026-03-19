"""Telegram bot command handlers."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import func, select
from telegram import Update
from telegram.ext import ContextTypes

from jobsearch import pipeline, scheduler, scraper, sheets
from jobsearch.config import get_settings
from jobsearch.db import get_db
from jobsearch.models import Job

STATE_EMOJIS = {
    "discovered": "🔍",
    "applied": "📤",
    "human_touched": "🤝",
    "screen": "📋",
    "loop": "🔄",
    "closed": "✅",
}


def _utcnow() -> datetime:
    """Return the current UTC time as a naive datetime."""

    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _run_manual_pipeline(bot) -> tuple[datetime, dict[str, int]]:
    """Run the shared scheduler pipeline for manual bot commands."""

    run_started_at = _utcnow()
    summary = await scheduler.run_pipeline(bot)
    return (run_started_at, summary)


async def _refresh_pipeline_sheet() -> None:
    """Rebuild the Pipeline worksheet after a stateful command mutation."""

    await asyncio.to_thread(pipeline.check_danger_states)
    roles = await asyncio.to_thread(pipeline.get_active_roles)
    await asyncio.to_thread(sheets.sync_pipeline_to_sheet, roles)
    await asyncio.to_thread(sheets.highlight_danger_rows)


def _format_timestamp(value: datetime | None) -> str:
    """Render a datetime for Telegram replies."""

    if value is None:
        return "none"
    return value.isoformat(sep=" ", timespec="seconds")


def _parse_contact_args(args: list[str]) -> tuple[str, str, str | None, str | None]:
    """Parse `/add_contact` arguments into role, name, role, and LinkedIn URL."""

    if len(args) < 2:
        raise ValueError("Usage: /add_contact <role_id> <name> [role] [linkedin_url]")

    role_id = args[0]
    remaining = args[1:]
    linkedin_url: str | None = None
    if remaining and remaining[-1].startswith(("http://", "https://")):
        linkedin_url = remaining[-1]
        remaining = remaining[:-1]

    contact_role: str | None = None
    if len(remaining) >= 2:
        contact_role = remaining[-1]
        name_parts = remaining[:-1]
    else:
        name_parts = remaining

    if not name_parts:
        raise ValueError("Contact name is required.")

    return (role_id, " ".join(name_parts), contact_role, linkedin_url)


def _scan_counts(run_started_at: datetime) -> tuple[Counter[str], int, int]:
    """Load run-scoped source and tier counts from the database."""

    with get_db() as session:
        rows = list(
            session.execute(
                select(Job.source, Job.tier).where(Job.scraped_at >= run_started_at)
            )
        )

    source_counts = Counter()
    tier_a = 0
    tier_b = 0
    for source, tier in rows:
        source_counts[str(source)] += 1
        if tier == "A":
            tier_a += 1
        elif tier == "B":
            tier_b += 1

    return (source_counts, tier_a, tier_b)


async def ping_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply to /ping with a liveness response."""

    if update.effective_message is None:
        return
    await update.effective_message.reply_text("pong 🟢")


async def scrape_now_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run the full scrape, score, and alert pipeline on demand."""

    if update.effective_message is None:
        return

    status_message = await update.effective_message.reply_text("Scraping… ⏳")
    _, summary = await _run_manual_pipeline(context.bot)

    await status_message.edit_text(
        "\n".join(
            (
                "✅ Done",
                f"Scraped: {summary['scraped']} new jobs",
                f"Embedded: {summary['embedded']}",
                f"Shortlisted: {summary['shortlisted']}",
                f"Scored: {summary['scored']}  |  Knocked out: {summary['knocked_out']}",
                f"Alerts sent: {summary['alerted']}",
            )
        )
    )


async def scan_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run the full pipeline and include a source breakdown for this run."""

    if update.effective_message is None:
        return

    status_message = await update.effective_message.reply_text("Scanning… ⏳")
    run_started_at, summary = await _run_manual_pipeline(context.bot)
    metrics = scraper.get_last_run_metrics()
    source_counts, tier_a, tier_b = _scan_counts(run_started_at)

    await status_message.edit_text(
        "\n".join(
            (
                "✅ Scan complete",
                f"📥 Found: {metrics['found']} jobs",
                f"✅ Passed filters: {metrics['passed_filters']}",
                f"🔴 Tier A: {tier_a}  🟡 Tier B: {tier_b}",
                f"✨ New: {summary['scraped']}",
                f"🧠 Embedded: {summary['embedded']}  📋 Shortlisted: {summary['shortlisted']}",
                "",
                "Sources:",
                (
                    f"lever: {source_counts.get('lever', 0)} | "
                    f"greenhouse: {source_counts.get('greenhouse', 0)} | "
                    f"ashby: {source_counts.get('ashby', 0)}"
                ),
                (
                    f"serp: 0 | "
                    f"llm_parsed: {source_counts.get('llm_parsed', 0)} | "
                    f"jobspy: 0"
                ),
            )
        )
    )


async def stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply with current scrape and alert counters."""

    if update.effective_message is None:
        return

    with get_db() as session:
        today_scraped = session.scalar(
            select(func.count(Job.id)).where(
                func.date(Job.scraped_at) == func.date("now"),
            )
        ) or 0
        today_tier_a = session.scalar(
            select(func.count(Job.id)).where(
                func.date(Job.scraped_at) == func.date("now"),
                Job.tier == "A",
            )
        ) or 0
        today_tier_b = session.scalar(
            select(func.count(Job.id)).where(
                func.date(Job.scraped_at) == func.date("now"),
                Job.tier == "B",
            )
        ) or 0
        pending_alerts = session.scalar(
            select(func.count(Job.id)).where(
                Job.tier.in_(("A", "B")),
                Job.alerted_at.is_(None),
                Job.knocked_out.is_(False),
                Job.llm_scored.is_(True),
            )
        ) or 0

    last_run = scheduler.get_last_pipeline_run()
    last_run_text = last_run.isoformat(sep=" ", timespec="seconds") if last_run else "never"

    await update.effective_message.reply_text(
        "\n".join(
            (
                "📊 Stats",
                f"Today scraped: {today_scraped}",
                f"Tier A: {today_tier_a}  |  Tier B: {today_tier_b}",
                f"Pending alerts: {pending_alerts}",
                f"Last pipeline run: {last_run_text}",
            )
        )
    )


async def sheet_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply with the Google Sheets job board URL."""

    del context

    if update.effective_message is None:
        return

    settings = get_settings()
    if not settings.google_sheet_id:
        await update.effective_message.reply_text("GOOGLE_SHEET_ID is not set.")
        return

    await update.effective_message.reply_text(f"📊 Job Board → {sheets.get_sheet_url()}")


async def pipeline_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List active pipeline roles in compact form."""

    del context

    if update.effective_message is None:
        return

    roles = await asyncio.to_thread(pipeline.get_active_roles)
    if not roles:
        await update.effective_message.reply_text("No active roles in pipeline.")
        return

    lines = []
    for role in roles:
        state_emoji = STATE_EMOJIS.get(role.state, "•")
        danger_emoji = " ⚠️" if role.danger_state else ""
        lines.append(f"{state_emoji} {role.company} — {role.title}{danger_emoji}")

    lines.append("")
    lines.append(f"Total: {len(roles)} active roles")
    await update.effective_message.reply_text("\n".join(lines))


async def apply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Create or reuse a pipeline role for one job and move it to applied."""

    if update.effective_message is None:
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /apply <job_id>")
        return

    job_id = context.args[0]
    with get_db() as session:
        job = session.get(Job, job_id)

    if job is None:
        await update.effective_message.reply_text(f"Job '{job_id}' not found.")
        return

    try:
        role = await asyncio.to_thread(
            pipeline.create_pipeline_role,
            job.id,
            job.company,
            job.title,
            job.url,
        )
        await asyncio.to_thread(pipeline.advance_state, role.id, "applied")
        await _refresh_pipeline_sheet()
    except ValueError as exc:
        await update.effective_message.reply_text(str(exc))
        return

    await update.effective_message.reply_text(
        f"📤 Added {job.title} at {job.company} to pipeline → Applied"
    )


async def advance_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Advance one pipeline role to the next state."""

    if update.effective_message is None:
        return
    if len(context.args) < 2:
        await update.effective_message.reply_text(
            "Usage: /advance <role_id> <new_state> [closed_reason]"
        )
        return

    role_id = context.args[0]
    new_state = context.args[1]
    closed_reason = " ".join(context.args[2:]).strip() or None

    try:
        role = await asyncio.to_thread(
            pipeline.advance_state,
            role_id,
            new_state,
            closed_reason,
        )
        await _refresh_pipeline_sheet()
    except ValueError as exc:
        await update.effective_message.reply_text(str(exc))
        return

    await update.effective_message.reply_text(
        f"{role.company} — {role.title} → {role.state}"
    )


async def add_contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add a new contact to one pipeline role."""

    if update.effective_message is None:
        return

    try:
        role_id, name, contact_role, linkedin_url = _parse_contact_args(context.args)
        contact = await asyncio.to_thread(
            pipeline.add_contact,
            role_id,
            name,
            contact_role,
            linkedin_url,
            None,
            None,
        )
        summary = await asyncio.to_thread(pipeline.get_role_summary, role_id)
        await _refresh_pipeline_sheet()
    except ValueError as exc:
        await update.effective_message.reply_text(str(exc))
        return

    await update.effective_message.reply_text(
        f"🤝 Contact {contact.name} added to {summary['company']} — {summary['title']}"
    )


async def log_outreach_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Record one outreach event against a pipeline role."""

    if update.effective_message is None:
        return
    if len(context.args) < 3:
        await update.effective_message.reply_text(
            "Usage: /log_outreach <role_id> <sent|received> <channel> [summary]"
        )
        return

    role_id, direction, channel = context.args[:3]
    summary_text = " ".join(context.args[3:]).strip() or None

    try:
        await asyncio.to_thread(
            pipeline.log_outreach,
            role_id,
            direction,
            channel,
            summary_text,
            None,
        )
        summary = await asyncio.to_thread(pipeline.get_role_summary, role_id)
        await _refresh_pipeline_sheet()
    except ValueError as exc:
        await update.effective_message.reply_text(str(exc))
        return

    await update.effective_message.reply_text(
        f"📝 Outreach logged for {summary['company']} — {summary['title']}"
    )


async def role_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show one pipeline role with contacts and outreach counts."""

    if update.effective_message is None:
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /role <role_id>")
        return

    role_id = context.args[0]
    try:
        summary = await asyncio.to_thread(pipeline.get_role_summary, role_id)
    except ValueError as exc:
        await update.effective_message.reply_text(str(exc))
        return

    contacts = ", ".join(contact["name"] for contact in summary["contacts"]) or "none"
    sent_count = sum(
        1 for entry in summary["outreach_log"] if entry["direction"] == "sent"
    )
    received_count = sum(
        1 for entry in summary["outreach_log"] if entry["direction"] == "received"
    )
    danger = summary["danger_state"] or "none"

    await update.effective_message.reply_text(
        "\n".join(
            (
                f"{summary['company']} — {summary['title']}",
                f"State: {summary['state']}",
                f"Danger: {danger}",
                f"Contacts: {contacts}",
                f"Outreach: sent {sent_count} | received {received_count}",
                f"Last activity: {_format_timestamp(summary['last_activity_at'])}",
            )
        )
    )


async def close_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Close one pipeline role."""

    if update.effective_message is None:
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /close <role_id> [reason]")
        return

    role_id = context.args[0]
    reason = " ".join(context.args[1:]).strip() or None

    try:
        role = await asyncio.to_thread(
            pipeline.advance_state,
            role_id,
            "closed",
            reason,
        )
        await _refresh_pipeline_sheet()
    except ValueError as exc:
        await update.effective_message.reply_text(str(exc))
        return

    await update.effective_message.reply_text(
        f"✅ {role.company} — {role.title} closed. Reason: {reason or 'none'}"
    )
