import asyncio
import logging

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import session_factory
from app.integrations.telegram import TelegramBot, TelegramClient
from app.jobs.digest import run_digest_loop
from app.jobs.gmail import force_poll_gmail, run_gmail_loop
from app.jobs.reminders import run_reminder_loop
from app.llm import LLMService, OpenAICompatibleProvider


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = logging.getLogger(__name__)
    logger.info("worker_started")
    if settings.telegram_mode == "polling" and settings.telegram_bot_token:
        if settings.telegram_owner_user_id is None:
            raise RuntimeError("TELEGRAM_OWNER_USER_ID is required for Telegram polling")
        client = TelegramClient(settings.telegram_bot_token)
        llm_provider = None
        llm_service = None
        if settings.llm_provider.lower() in {"openclaw", "openai-compatible"}:
            llm_provider = OpenAICompatibleProvider(
                settings.openclaw_base_url,
                settings.openclaw_api_key,
                settings.llm_timeout_seconds,
            )
            llm_service = LLMService(
                llm_provider,
                settings.llm_model,
                settings.llm_temperature,
                settings.timezone,
            )
        gmail_poll_callback = None
        if llm_service is not None:
            async def gmail_poll_callback() -> int:
                return await force_poll_gmail(
                    session_factory,
                    client,
                    settings.telegram_owner_user_id,
                    llm_service,
                    settings,
                )

        bot = TelegramBot(
            client,
            session_factory,
            settings.telegram_owner_user_id,
            llm_service,
            settings.timezone,
            gmail_poll=gmail_poll_callback,
        )
        digest_task = asyncio.create_task(
            run_digest_loop(
                session_factory,
                client,
                settings.telegram_owner_user_id,
                settings.timezone,
            )
        )
        reminder_task = asyncio.create_task(
            run_reminder_loop(
                session_factory,
                client,
                settings.telegram_owner_user_id,
                settings.timezone,
            )
        )
        gmail_task = (
            asyncio.create_task(
                run_gmail_loop(
                    session_factory,
                    client,
                    settings.telegram_owner_user_id,
                    llm_service,
                    settings,
                )
            )
            if settings.gmail_enabled
            else None
        )
        try:
            await bot.run_polling()
        finally:
            digest_task.cancel()
            reminder_task.cancel()
            if gmail_task is not None:
                gmail_task.cancel()
            await asyncio.gather(
                digest_task,
                reminder_task,
                *(task for task in (gmail_task,) if task is not None),
                return_exceptions=True,
            )
            await client.close()
            if llm_provider is not None:
                await llm_provider.close()
        return
    logger.info("telegram_polling_disabled")
    while True:
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(run())
