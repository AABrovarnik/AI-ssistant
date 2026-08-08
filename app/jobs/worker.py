import asyncio
import logging

from app.core.config import get_settings
from app.core.logging import configure_logging


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = logging.getLogger(__name__)
    logger.info("worker_started")
    while True:
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(run())
