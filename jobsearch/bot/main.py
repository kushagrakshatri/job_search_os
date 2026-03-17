"""Telegram bot application entry point."""

from __future__ import annotations

import sys

import structlog
from telegram.ext import Application, ApplicationBuilder, CallbackQueryHandler, CommandHandler

from jobsearch.bot.commands import (
    callback_handler,
    ping_handler,
    scrape_now_handler,
    stats_handler,
)
from jobsearch.config import get_settings
from jobsearch.scheduler import start

logger = structlog.get_logger(__name__)


def build_application() -> Application:
    """Build the Telegram application and register handlers."""

    settings = get_settings()
    application = ApplicationBuilder().token(settings.TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("ping", ping_handler))
    application.add_handler(CommandHandler("scrape_now", scrape_now_handler))
    application.add_handler(CommandHandler("stats", stats_handler))
    application.add_handler(
        CallbackQueryHandler(
            callback_handler,
            pattern=r"^(add_pipeline|dismiss):",
        )
    )
    return application


def main() -> None:
    """Start the Telegram bot and its scheduler."""

    settings = get_settings()
    if not settings.TELEGRAM_TOKEN:
        print("TELEGRAM_TOKEN is not set.", file=sys.stderr)
        raise SystemExit(1)

    application = build_application()
    start(application.bot)
    logger.info("bot_started")
    application.run_polling()


if __name__ == "__main__":
    main()
