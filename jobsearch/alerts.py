"""Telegram alert formatting and delivery for scored jobs."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import TelegramError

from jobsearch.config import get_settings
from jobsearch.db import get_db
from jobsearch.models import Job

logger = structlog.get_logger(__name__)

DIVIDER = "━━━━━━━━━━━━━━━━━━━━━"
MARKDOWN_V2_SPECIAL_CHARS = set(r"_*[]()~`>#+-=|{}.!\\")
SCORE_LAYOUT: tuple[tuple[str, str, int], ...] = (
    ("Tech Stack", "score_tech_stack", 25),
    ("Role Fit", "score_role_fit", 20),
    ("Work Auth", "score_work_auth", 20),
    ("Interviewable", "score_interviewability", 15),
    ("AI Signal", "score_ai_signal", 10),
    ("Growth", "score_growth", 10),
)
TIER_EMOJIS = {
    "A": "🔴",
    "B": "🟠",
}


def _utcnow() -> datetime:
    """Return the current UTC time as a naive datetime."""

    return datetime.now(timezone.utc).replace(tzinfo=None)


def escape_markdown_v2(text: str) -> str:
    """Escape dynamic content for Telegram MarkdownV2."""

    escaped: list[str] = []
    for character in text:
        if character in MARKDOWN_V2_SPECIAL_CHARS:
            escaped.append("\\")
        escaped.append(character)
    return "".join(escaped)


def _score_value(value: int | None) -> int:
    """Normalize nullable score fields for rendering."""

    return 0 if value is None else value


def _truncate_url(url: str, width: int = 60) -> str:
    """Display only the leading slice of a URL."""

    return url[:width]


def _format_progress_bar(score: int, maximum: int, width: int = 10) -> str:
    """Render a fixed-width Unicode progress bar."""

    filled = round((score / maximum) * width) if maximum else 0
    filled = max(0, min(width, filled))
    return ("█" * filled) + ("░" * (width - filled))


def _format_score_line(label: str, score: int, maximum: int, suffix: str = "") -> str:
    """Render one labeled score line."""

    bar = _format_progress_bar(score, maximum)
    return f"{label.ljust(14)} {bar}  {score:>2}/{maximum}{suffix}"


def format_alert(job: Job) -> str:
    """Build the MarkdownV2 alert body for a scored job."""

    tier = job.tier or "?"
    emoji = TIER_EMOJIS.get(tier, "⚪")
    score_total = _score_value(job.total_score)
    title_company = (
        f"{escape_markdown_v2(job.title)} — {escape_markdown_v2(job.company)}"
    )
    location = escape_markdown_v2(job.location)
    if job.is_remote:
        location_line = f"📍 {location}  \\(Remote OK\\)"
    else:
        location_line = f"📍 {location}"
    url_line = f"🔗 {escape_markdown_v2(_truncate_url(job.url))}"

    lines = [
        f"{emoji} TIER {tier}  \\|  {score_total}pts",
        DIVIDER,
        title_company,
        location_line,
        url_line,
        "",
    ]

    for label, attribute, maximum in SCORE_LAYOUT:
        score = _score_value(getattr(job, attribute))
        suffix = " ✅" if attribute == "score_work_auth" and score >= 16 else ""
        lines.append(_format_score_line(label, score, maximum, suffix=suffix))

    lines.append(DIVIDER)
    return "\n".join(lines)


def build_keyboard(job_id: str) -> InlineKeyboardMarkup:
    """Build the alert inline keyboard."""

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    text="➕ Add to pipeline",
                    callback_data=f"add_pipeline:{job_id}",
                ),
                InlineKeyboardButton(
                    text="🔕 Dismiss",
                    callback_data=f"dismiss:{job_id}",
                ),
            ]
        ]
    )


async def send_alert(job: Job, bot: Bot) -> bool:
    """Send one alert and persist alerted_at on success."""

    settings = get_settings()

    try:
        await bot.send_message(
            chat_id=settings.CHAT_ID,
            text=format_alert(job),
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=build_keyboard(job.id),
        )
    except TelegramError as exc:
        logger.warning("send_alert_failed", job_id=job.id, error=str(exc))
        return False

    with get_db() as session:
        db_job = session.get(Job, job.id)
        if db_job is None:
            logger.warning("send_alert_missing_db_row", job_id=job.id)
            return False

        db_job.alerted_at = _utcnow()
        session.commit()
    return True


async def send_pending_alerts(bot: Bot) -> int:
    """Send all pending Tier A and Tier B alerts."""

    with get_db() as session:
        jobs = list(
            session.scalars(
                select(Job)
                .where(
                    Job.tier.in_(("A", "B")),
                    Job.alerted_at.is_(None),
                    Job.knocked_out.is_(False),
                    Job.llm_scored.is_(True),
                )
                .order_by(Job.total_score.desc(), Job.scraped_at.asc(), Job.id.asc())
            )
        )

    sent_count = 0
    for job in jobs:
        if await send_alert(job, bot):
            sent_count += 1

    return sent_count


def main() -> None:
    """Send pending alerts using the configured Telegram bot token."""

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
