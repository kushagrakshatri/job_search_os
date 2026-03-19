"""Telegram summary notifications and Google Sheets board sync."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from telegram import Bot
from telegram.error import TelegramError

from jobsearch import sheets
from jobsearch.config import get_settings
from jobsearch.db import get_db
from jobsearch.models import Job

logger = structlog.get_logger(__name__)


def _utcnow() -> datetime:
    """Return the current UTC time as a naive datetime."""

    return datetime.now(timezone.utc).replace(tzinfo=None)


async def send_telegram_message(bot: Bot, text: str) -> bool:
    """Send a plain-text Telegram message and log failures."""

    settings = get_settings()

    try:
        await bot.send_message(
            chat_id=settings.CHAT_ID,
            text=text,
        )
    except TelegramError as exc:
        logger.warning("telegram_message_failed", error=str(exc))
        return False

    return True


async def send_pending_alerts(bot: Bot) -> int:
    """Append pending jobs to the sheet and send one Telegram summary message."""

    with get_db() as session:
        jobs = list(
            session.scalars(
                select(Job)
                .where(
                    Job.tier.in_(("A", "B")),
                    Job.alerted_at.is_(None),
                )
                .order_by(Job.total_score.desc(), Job.id.asc())
            )
        )

    if not jobs:
        return 0

    sheets.append_jobs_to_sheet(jobs)

    alerted_at = _utcnow()
    job_ids = [job.id for job in jobs]
    with get_db() as session:
        db_jobs = list(session.scalars(select(Job).where(Job.id.in_(job_ids))))
        for job in db_jobs:
            job.alerted_at = alerted_at
        session.commit()

    tier_a = sum(1 for job in jobs if job.tier == "A")
    tier_b = sum(1 for job in jobs if job.tier == "B")
    await send_telegram_message(
        bot,
        "\n".join(
            (
                "✅ Pipeline complete",
                f"Tier A: {tier_a} | Tier B: {tier_b}",
                sheets.get_sheet_url(),
            )
        ),
    )
    return len(jobs)


def main() -> None:
    """Send the pending pipeline summary using the configured Telegram bot."""

    settings = get_settings()
    if not settings.TELEGRAM_TOKEN:
        print("TELEGRAM_TOKEN is not set.", file=sys.stderr)
        raise SystemExit(1)
    if not settings.CHAT_ID:
        print("CHAT_ID is not set.", file=sys.stderr)
        raise SystemExit(1)

    bot = Bot(token=settings.TELEGRAM_TOKEN)

    async def runner() -> int:
        await bot.initialize()
        try:
            return await send_pending_alerts(bot)
        finally:
            await bot.shutdown()

    print(asyncio.run(runner()))


if __name__ == "__main__":
    main()
