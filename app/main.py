import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes.calendar import router as calendar_router
from app.api.routes.gmail import router as gmail_router
from app.api.routes.health import router as health_router
from app.api.routes.tasks import router as tasks_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.dashboard.routes import api_router as dashboard_api_router
from app.dashboard.routes import web_router as dashboard_web_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    logging.getLogger(__name__).info("application_started", extra={"service": settings.app_name})
    yield


settings = get_settings()
app = FastAPI(title="AI Secretary", version="0.1.0", lifespan=lifespan)
app.include_router(health_router)
app.include_router(tasks_router)
app.include_router(gmail_router)
app.include_router(calendar_router)
app.include_router(dashboard_api_router)
app.include_router(dashboard_web_router)
