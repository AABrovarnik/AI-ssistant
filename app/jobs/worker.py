import asyncio
import logging

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import session_factory
from app.integrations.telegram import TelegramBot, TelegramClient


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = logging.getLogger(__name__)
    logger.info("worker_started")
    if settings.telegram_mode == "polling" and settings.telegram_bot_token:
        if settings.telegram_owner_user_id is None:
            raise RuntimeError("TELEGRAM_OWNER_USER_ID is required for Telegram polling")
        client = TelegramClient(settings.telegram_bot_token)
        bot = TelegramBot(client, session_factory, settings.telegram_owner_user_id)
        try:
            await bot.run_polling()
        finally:
            await client.close()
        return
    logger.info("telegram_polling_disabled")
    while True:
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(run())
