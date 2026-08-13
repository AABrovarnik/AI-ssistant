"""Explicit Google Calendar OAuth and connection endpoints."""
# ruff: noqa: B008

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import require_internal_api_token
from app.db.session import get_db_session
from app.integrations.calendar.accounts import save_calendar_oauth_token
from app.integrations.calendar.client import CalendarOAuthClient, CalendarOAuthError
from app.integrations.gmail.oauth_state import OAuthStateError, create_state, verify_state
from app.integrations.gmail.security import TokenCipher, TokenEncryptionError
from app.tasks.models import IntegrationAccount, IntegrationProvider
from app.tasks.service import TaskService

router = APIRouter(prefix="/integrations/calendar", tags=["calendar"])


def _oauth_client() -> CalendarOAuthClient:
    settings = get_settings()
    if not settings.google_calendar_enabled:
        raise HTTPException(status_code=503, detail="Google Calendar integration is disabled")
    if not settings.gmail_client_id or not settings.gmail_client_secret:
        raise HTTPException(status_code=503, detail="Google OAuth credentials are not configured")
    return CalendarOAuthClient(
        settings.gmail_client_id,
        settings.gmail_client_secret,
        settings.google_calendar_redirect_uri,
    )


@router.get("/authorize", dependencies=[Depends(require_internal_api_token)])
async def calendar_authorize(session: AsyncSession = Depends(get_db_session)) -> dict[str, str]:
    settings = get_settings()
    client = _oauth_client()
    user = await TaskService(session).ensure_user()
    await session.commit()
    state = create_state(str(user.id), settings.internal_api_token)
    try:
        return {"authorization_url": client.authorization_url(state)}
    finally:
        await client.close()


@router.get("/callback")
async def calendar_callback(
    code: str = Query(min_length=1),
    state: str = Query(min_length=1),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    settings = get_settings()
    try:
        user_id = UUID(verify_state(state, settings.internal_api_token))
        cipher = TokenCipher(settings.token_encryption_key)
    except (OAuthStateError, ValueError, TokenEncryptionError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    client = _oauth_client()
    try:
        token = await client.exchange_code(code)
    except CalendarOAuthError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    finally:
        await client.close()
    await save_calendar_oauth_token(session, user_id, token, cipher)
    await session.commit()
    return {"status": "connected", "provider": "GOOGLE_CALENDAR"}


@router.get("/status", dependencies=[Depends(require_internal_api_token)])
async def calendar_status(session: AsyncSession = Depends(get_db_session)) -> dict[str, str | None]:
    user = await TaskService(session).ensure_user()
    account = await session.scalar(
        select(IntegrationAccount).where(
            IntegrationAccount.user_id == user.id,
            IntegrationAccount.provider == IntegrationProvider.GOOGLE_CALENDAR,
        )
    )
    return {
        "provider": "GOOGLE_CALENDAR",
        "status": str(account.status) if account is not None else "DISCONNECTED",
        "last_synced_at": account.updated_at.isoformat() if account is not None else None,
    }
