"""Persistence helpers for encrypted Google Calendar OAuth credentials."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.calendar.client import CalendarOAuthToken
from app.integrations.gmail.security import TokenCipher
from app.tasks.models import IntegrationAccount, IntegrationProvider, IntegrationStatus


async def save_calendar_oauth_token(
    session: AsyncSession,
    user_id: UUID,
    token: CalendarOAuthToken,
    cipher: TokenCipher,
) -> IntegrationAccount:
    account = await session.scalar(
        select(IntegrationAccount).where(
            IntegrationAccount.user_id == user_id,
            IntegrationAccount.provider == IntegrationProvider.GOOGLE_CALENDAR,
        )
    )
    if account is None:
        account = IntegrationAccount(
            user_id=user_id,
            provider=IntegrationProvider.GOOGLE_CALENDAR,
            access_token_encrypted=cipher.encrypt(token.access_token),
            refresh_token_encrypted=(
                cipher.encrypt(token.refresh_token) if token.refresh_token else None
            ),
            scopes=token.scope,
            token_expires_at=_expires_at(token.expires_in),
            status=IntegrationStatus.CONNECTED,
        )
        session.add(account)
    else:
        account.access_token_encrypted = cipher.encrypt(token.access_token)
        if token.refresh_token:
            account.refresh_token_encrypted = cipher.encrypt(token.refresh_token)
        account.scopes = token.scope
        account.token_expires_at = _expires_at(token.expires_in)
        account.status = IntegrationStatus.CONNECTED
        account.error_message = None
    await session.flush()
    return account


def _expires_at(expires_in: int | None) -> datetime | None:
    if expires_in is None:
        return None
    return datetime.now(UTC) + timedelta(seconds=expires_in)
