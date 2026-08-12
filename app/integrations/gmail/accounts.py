"""Persistence helpers for encrypted Gmail OAuth credentials."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.gmail.client import GmailOAuthToken
from app.integrations.gmail.security import TokenCipher
from app.tasks.models import IntegrationAccount, IntegrationProvider, IntegrationStatus


async def save_gmail_oauth_token(
    session: AsyncSession,
    user_id: UUID,
    token: GmailOAuthToken,
    cipher: TokenCipher,
    external_account_id: str | None = None,
) -> IntegrationAccount:
    account = await session.scalar(
        select(IntegrationAccount).where(
            IntegrationAccount.user_id == user_id,
            IntegrationAccount.provider == IntegrationProvider.GMAIL,
        )
    )
    if account is None:
        account = IntegrationAccount(
            user_id=user_id,
            provider=IntegrationProvider.GMAIL,
            access_token_encrypted=cipher.encrypt(token.access_token),
            refresh_token_encrypted=(
                cipher.encrypt(token.refresh_token) if token.refresh_token else None
            ),
            external_account_id=external_account_id,
            scopes=token.scope,
            token_expires_at=_expires_at(token.expires_in),
            status=IntegrationStatus.CONNECTED,
        )
        session.add(account)
    else:
        account.access_token_encrypted = cipher.encrypt(token.access_token)
        if token.refresh_token:
            account.refresh_token_encrypted = cipher.encrypt(token.refresh_token)
        account.external_account_id = external_account_id or account.external_account_id
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
