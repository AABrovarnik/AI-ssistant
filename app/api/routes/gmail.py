"""Read-only Gmail OAuth connection endpoints."""
# ruff: noqa: B008

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import require_internal_api_token
from app.db.session import get_db_session, session_factory
from app.integrations.gmail.accounts import save_gmail_oauth_token
from app.integrations.gmail.client import GmailOAuthClient, GmailOAuthError
from app.integrations.gmail.oauth_state import OAuthStateError, create_state, verify_state
from app.integrations.gmail.security import TokenCipher, TokenEncryptionError
from app.integrations.telegram.client import TelegramClient
from app.jobs.gmail import force_poll_gmail
from app.llm import LLMService, OpenAICompatibleProvider
from app.tasks.models import IntegrationAccount, IntegrationProvider
from app.tasks.service import TaskService

router = APIRouter(prefix="/integrations/gmail", tags=["gmail"])


def _oauth_client() -> GmailOAuthClient:
    settings = get_settings()
    if not settings.gmail_client_id or not settings.gmail_client_secret:
        raise HTTPException(status_code=503, detail="Gmail OAuth credentials are not configured")
    return GmailOAuthClient(
        settings.gmail_client_id,
        settings.gmail_client_secret,
        settings.gmail_redirect_uri,
    )


@router.get("/authorize", dependencies=[Depends(require_internal_api_token)])
async def gmail_authorize(session: AsyncSession = Depends(get_db_session)) -> dict[str, str]:
    settings = get_settings()
    client = _oauth_client()
    user = await TaskService(session).ensure_user()
    await session.commit()
    state = create_state(str(user.id), settings.internal_api_token)
    return {"authorization_url": client.authorization_url(state)}


@router.get("/callback")
async def gmail_callback(
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
    except GmailOAuthError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    finally:
        await client.close()
    await save_gmail_oauth_token(session, user_id, token, cipher)
    await session.commit()
    return {"status": "connected", "provider": "GMAIL"}


@router.get("/status", dependencies=[Depends(require_internal_api_token)])
async def gmail_status(session: AsyncSession = Depends(get_db_session)) -> dict[str, str | None]:
    user = await TaskService(session).ensure_user()
    account = await session.scalar(
        select(IntegrationAccount).where(
            IntegrationAccount.user_id == user.id,
            IntegrationAccount.provider == IntegrationProvider.GMAIL,
        )
    )
    return {
        "provider": "GMAIL",
        "status": str(account.status) if account is not None else "DISCONNECTED",
        "external_account_id": account.external_account_id if account is not None else None,
        "last_polled_at": (
            account.last_polled_at.isoformat()
            if account and account.last_polled_at
            else None
        ),
    }


@router.post("/poll", dependencies=[Depends(require_internal_api_token)])
async def gmail_poll() -> dict[str, int | str]:
    """Run an immediate Gmail poll and notify the owner about candidates."""

    settings = get_settings()
    if not settings.gmail_enabled:
        raise HTTPException(status_code=503, detail="Gmail integration is disabled")
    if not settings.gmail_client_id or not settings.gmail_client_secret:
        raise HTTPException(status_code=503, detail="Gmail OAuth credentials are not configured")
    if not settings.telegram_bot_token or settings.telegram_owner_user_id is None:
        raise HTTPException(
            status_code=503, detail="Telegram polling credentials are not configured"
        )
    if settings.llm_provider.lower() not in {"openclaw", "openai-compatible"}:
        raise HTTPException(status_code=503, detail="Gmail LLM provider is not configured")

    telegram = TelegramClient(settings.telegram_bot_token)
    provider = OpenAICompatibleProvider(
        settings.openclaw_base_url,
        settings.openclaw_api_key,
        settings.llm_timeout_seconds,
    )
    try:
        processed = await force_poll_gmail(
            session_factory,
            telegram,
            settings.telegram_owner_user_id,
            LLMService(provider, settings.llm_model, settings.llm_temperature, settings.timezone),
            settings,
        )
        return {"status": "ok", "candidates_notified": processed}
    except TokenEncryptionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        await provider.close()
        await telegram.close()
