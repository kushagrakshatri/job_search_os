"""Telegram bot command and callback handlers."""

from __future__ import annotations

import asyncio

from sqlalchemy import func, select
from telegram import Update
from telegram.ext import ContextTypes

from jobsearch import alerts, scheduler, scraper, scorer
from jobsearch.config import load_config
from jobsearch.db import get_db
from jobsearch.models import Job


async def ping_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply to /ping with a liveness response."""

    if update.effective_message is None:
        return
    await update.effective_message.reply_text("pong 🟢")


async def scrape_now_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run the full scrape, score, and alert pipeline on demand."""

    if update.effective_message is None:
        return

    app_config = load_config()
    status_message = await update.effective_message.reply_text("Scraping… ⏳")
    new_jobs = await asyncio.to_thread(
        scraper.fetch_all,
        app_config.scraper.sources,
        app_config.scraper.search_terms,
        app_config.scraper.results_wanted_per_source,
    )
    success, failed = await asyncio.to_thread(scorer.score_pending, 50)
    sent = await alerts.send_pending_alerts(context.bot)
    scheduler.mark_pipeline_run()

    await status_message.edit_text(
        "\n".join(
            (
                "✅ Done",
                f"Scraped: {len(new_jobs)} new jobs",
                f"Scored: {success} ({failed} failed)",
                f"Alerts sent: {sent}",
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


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Answer alert button callbacks with the Module 2 placeholder."""

    if update.callback_query is None:
        return
    await update.callback_query.answer("Coming in Module 2 🔜")
