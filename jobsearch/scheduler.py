"""Scheduler orchestration for scrape, score, and alert runs."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import structlog
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Bot
from telegram.error import TelegramError

from jobsearch import alerts, scraper, scorer
from jobsearch.codex_client import get_access_token
from jobsearch.config import get_settings, load_config
from jobsearch.db import DATABASE_URL

logger = structlog.get_logger(__name__)

_last_pipeline_run_at: datetime | None = None
_scheduler: AsyncIOScheduler | None = None
_runtime_bot: Bot | None = None


def _utcnow() -> datetime:
    """Return the current UTC time as a naive datetime."""

    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_last_pipeline_run() -> datetime | None:
    """Return the last completed pipeline run timestamp."""

    return _last_pipeline_run_at


def mark_pipeline_run(timestamp: datetime | None = None) -> datetime:
    """Persist the last completed pipeline run timestamp in memory."""

    global _last_pipeline_run_at
    _last_pipeline_run_at = timestamp or _utcnow()
    return _last_pipeline_run_at


async def run_pipeline(bot: Bot) -> dict[str, int]:
    """Run one full scrape -> score -> alert pipeline cycle."""

    app_config = load_config()
    new_jobs = await asyncio.to_thread(
        scraper.fetch_all,
        app_config.scraper.sources,
        app_config.scraper.search_terms,
        app_config.scraper.results_wanted_per_source,
    )
    success, failed = await asyncio.to_thread(scorer.score_pending, 50)
    sent = await alerts.send_pending_alerts(bot)
    mark_pipeline_run()
    logger.info(
        "pipeline_run_completed",
        scraped=len(new_jobs),
        scored=success,
        failed=failed,
        alerted=sent,
    )
    return {
        "scraped": len(new_jobs),
        "scored": success,
        "failed": failed,
        "alerted": sent,
    }


async def _scheduled_pipeline_job() -> None:
    """Run the scheduled pipeline with Codex token expiry protection."""

    if _runtime_bot is None:
        logger.warning("scheduled_pipeline_missing_bot")
        return

    settings = get_settings()
    try:
        get_access_token()
    except RuntimeError as exc:
        logger.warning("codex_token_expired", error=str(exc))
        if settings.CHAT_ID:
            try:
                await _runtime_bot.send_message(
                    chat_id=settings.CHAT_ID,
                    text=(
                        "⚠️ Codex token expired — run `codex` in terminal to refresh, "
                        "then restart the bot."
                    ),
                )
            except TelegramError as telegram_exc:
                logger.warning(
                    "codex_token_expired_notify_failed",
                    error=str(telegram_exc),
                )
        return

    await run_pipeline(_runtime_bot)


def _build_trigger(cron_expression: str, timezone_name: str) -> CronTrigger:
    """Convert a cron string into an APScheduler trigger."""

    return CronTrigger.from_crontab(
        cron_expression,
        timezone=ZoneInfo(timezone_name),
    )


def start(bot: Bot) -> AsyncIOScheduler:
    """Create and start the scheduler for recurring pipeline runs."""

    global _scheduler, _runtime_bot
    _runtime_bot = bot
    if _scheduler is not None:
        return _scheduler

    settings = get_settings()
    jobstores = {"default": SQLAlchemyJobStore(url=DATABASE_URL)}
    scheduler = AsyncIOScheduler(jobstores=jobstores)

    morning_trigger = _build_trigger(
        settings.SCRAPE_SCHEDULE_MORNING,
        settings.SCHEDULER_TIMEZONE,
    )
    evening_trigger = _build_trigger(
        settings.SCRAPE_SCHEDULE_EVENING,
        settings.SCHEDULER_TIMEZONE,
    )

    scheduler.add_job(
        _scheduled_pipeline_job,
        trigger=morning_trigger,
        id="pipeline_morning",
        replace_existing=True,
    )
    scheduler.add_job(
        _scheduled_pipeline_job,
        trigger=evening_trigger,
        id="pipeline_evening",
        replace_existing=True,
    )

    logger.info(
        "scheduler_job_registered",
        job_id="pipeline_morning",
        schedule=settings.SCRAPE_SCHEDULE_MORNING,
        trigger=str(morning_trigger),
    )
    logger.info(
        "scheduler_job_registered",
        job_id="pipeline_evening",
        schedule=settings.SCRAPE_SCHEDULE_EVENING,
        trigger=str(evening_trigger),
    )

    scheduler.start()
    logger.info("scheduler_started")
    _scheduler = scheduler
    return scheduler
