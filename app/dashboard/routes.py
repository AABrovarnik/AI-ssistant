"""Dashboard authentication, JSON API and lightweight web UI."""
# ruff: noqa: B008

from __future__ import annotations

import hashlib
import hmac
import time as time_module
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import bearer
from app.dashboard.service import (
    DashboardPeriod,
    DashboardQueryService,
    DashboardTimezoneError,
    make_period,
)
from app.db.session import get_db_session
from app.integrations.gmail.rules import (
    DEFAULT_CLASSIFICATION_THRESHOLD,
    GmailClassificationRule,
    gmail_config,
)
from app.tasks.models import UserSettings
from app.tasks.service import SYSTEM_USER_ID

COOKIE_NAME = "ai_secretary_dashboard"
COOKIE_TTL_SECONDS = 3600
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

web_router = APIRouter(prefix="/dashboard", tags=["dashboard-ui"])
api_router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class DashboardLoginRequest(BaseModel):
    token: str = Field(min_length=1)


class GmailSettingsRequest(BaseModel):
    sender_allowlist: list[str] = Field(default_factory=list, max_length=200)
    sender_blocklist: list[str] = Field(default_factory=list, max_length=200)
    vip_senders: list[str] = Field(default_factory=list, max_length=200)
    ignore_newsletters: bool = True
    classification_threshold: float = Field(
        default=DEFAULT_CLASSIFICATION_THRESHOLD, ge=0, le=1
    )
    classification_rules: list[GmailClassificationRule] = Field(
        default_factory=list, max_length=100
    )


def _signature(timestamp: int) -> str:
    settings = get_settings()
    message = f"dashboard:{timestamp}".encode()
    return hmac.new(settings.internal_api_token.encode(), message, hashlib.sha256).hexdigest()


def _make_session() -> str:
    timestamp = int(time_module.time())
    return f"{timestamp}.{_signature(timestamp)}"


def _valid_session(value: str | None) -> bool:
    if not value or "." not in value:
        return False
    timestamp_text, signature = value.split(".", 1)
    try:
        timestamp = int(timestamp_text)
    except ValueError:
        return False
    if abs(int(time_module.time()) - timestamp) > COOKIE_TTL_SECONDS:
        return False
    return hmac.compare_digest(signature, _signature(timestamp))


def _valid_bearer(credentials: HTTPAuthorizationCredentials | None) -> bool:
    settings = get_settings()
    return bool(
        credentials
        and credentials.scheme.lower() == "bearer"
        and hmac.compare_digest(credentials.credentials, settings.internal_api_token)
    )


async def require_dashboard_access(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> None:
    if _valid_bearer(credentials) or _valid_session(request.cookies.get(COOKIE_NAME)):
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="dashboard authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _period(
    from_date: date | None,
    to_date: date | None,
    timezone: str,
) -> DashboardPeriod:
    try:
        return make_period(from_date, to_date, timezone)
    except (DashboardTimezoneError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _service(
    session: AsyncSession,
    from_date: date | None,
    to_date: date | None,
    timezone: str,
) -> DashboardQueryService:
    return DashboardQueryService(session, _period(from_date, to_date, timezone))


@web_router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_login_page(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(request, "login.html", {})


@web_router.post("/login")
async def dashboard_login(data: DashboardLoginRequest, response: Response) -> dict[str, str]:
    settings = get_settings()
    if not hmac.compare_digest(data.token, settings.internal_api_token):
        raise HTTPException(status_code=401, detail="invalid dashboard token")
    response.set_cookie(
        COOKIE_NAME,
        _make_session(),
        max_age=COOKIE_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=get_settings().app_env.lower() not in {"development", "test"},
    )
    return {"status": "ok"}


@web_router.post("/logout")
async def dashboard_logout(response: Response) -> dict[str, str]:
    response.delete_cookie(COOKIE_NAME)
    return {"status": "ok"}


@web_router.get("", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_page(request: Request) -> Response:
    credentials = request.headers.get("authorization", "")
    bearer_token = credentials.removeprefix("Bearer ").strip() if credentials else None
    if not _valid_session(request.cookies.get(COOKIE_NAME)) and not hmac.compare_digest(
        bearer_token or "", get_settings().internal_api_token
    ):
        return RedirectResponse("/dashboard/login", status_code=303)
    return TEMPLATES.TemplateResponse(request, "dashboard.html", {})


async def _get_owner_gmail_settings(session: AsyncSession) -> UserSettings:
    settings = await session.get(UserSettings, SYSTEM_USER_ID)
    if settings is None:
        settings = UserSettings(user_id=SYSTEM_USER_ID)
        session.add(settings)
        await session.flush()
    return settings


@api_router.get("/gmail/settings", dependencies=[Depends(require_dashboard_access)])
async def dashboard_gmail_settings(
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    settings = await _get_owner_gmail_settings(session)
    config = gmail_config(settings)
    payload = GmailSettingsRequest.model_validate(
        {
            "sender_allowlist": config.get("sender_allowlist", []),
            "sender_blocklist": config.get("sender_blocklist", []),
            "vip_senders": config.get("vip_senders", []),
            "ignore_newsletters": config.get("ignore_newsletters", True),
            "classification_threshold": config.get(
                "classification_threshold", DEFAULT_CLASSIFICATION_THRESHOLD
            ),
            "classification_rules": config.get("classification_rules", []),
        }
    )
    return payload.model_dump(mode="json")


@api_router.put("/gmail/settings", dependencies=[Depends(require_dashboard_access)])
async def update_dashboard_gmail_settings(
    data: GmailSettingsRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    settings = await _get_owner_gmail_settings(session)
    existing = dict(settings.extra or {})
    existing["gmail"] = data.model_dump(mode="json")
    settings.extra = existing
    await session.commit()
    return data.model_dump(mode="json")


@api_router.get("/overview", dependencies=[Depends(require_dashboard_access)])
async def dashboard_overview(
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    timezone: str = Query(default="UTC"),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    return await _service(session, from_date, to_date, timezone).overview()


@api_router.get("/timeseries", dependencies=[Depends(require_dashboard_access)])
async def dashboard_timeseries(
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    timezone: str = Query(default="UTC"),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    return await _service(session, from_date, to_date, timezone).timeseries()


@api_router.get("/messages", dependencies=[Depends(require_dashboard_access)])
async def dashboard_messages(
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    timezone: str = Query(default="UTC"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    service = _service(session, from_date, to_date, timezone)
    return {"items": await service.messages(limit, offset), "limit": limit, "offset": offset}


@api_router.get("/candidates", dependencies=[Depends(require_dashboard_access)])
async def dashboard_candidates(
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    timezone: str = Query(default="UTC"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    service = _service(session, from_date, to_date, timezone)
    return {"items": await service.candidates(limit, offset), "limit": limit, "offset": offset}


@api_router.get("/candidates/{candidate_id}", dependencies=[Depends(require_dashboard_access)])
async def dashboard_candidate_detail(
    candidate_id: UUID,
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    timezone: str = Query(default="UTC"),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    item = await _service(session, from_date, to_date, timezone).candidate_detail(candidate_id)
    if item is None:
        raise HTTPException(status_code=404, detail="candidate not found")
    return item


@api_router.get("/tasks", dependencies=[Depends(require_dashboard_access)])
async def dashboard_tasks(
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    timezone: str = Query(default="UTC"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    service = _service(session, from_date, to_date, timezone)
    return {"items": await service.tasks(limit, offset), "limit": limit, "offset": offset}


@api_router.get("/operations", dependencies=[Depends(require_dashboard_access)])
async def dashboard_operations(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    service = _service(session, None, None, "UTC")
    return {"items": await service.operations(limit, offset), "limit": limit, "offset": offset}


@api_router.get("/poll-runs", dependencies=[Depends(require_dashboard_access)])
async def dashboard_poll_runs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    service = _service(session, None, None, "UTC")
    return {"items": await service.operations(limit, offset), "limit": limit, "offset": offset}
