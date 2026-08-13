"""Periodic read-only Gmail polling."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.integrations.gmail.accounts import save_gmail_oauth_token
from app.integrations.gmail.client import GmailAPIClient, GmailOAuthClient
from app.integrations.gmail.security import TokenCipher, TokenEncryptionError
from app.integrations.gmail.service import GmailInboxService, notify_gmail_candidate
from app.integrations.telegram.client import TelegramClientProtocol
from app.llm.service import LLMService
from app.tasks.models import (
    IntegrationAccount,
    IntegrationProvider,
    IntegrationStatus,
    UserSettings,
)

logger = logging.getLogger(__name__)


async def run_gmail_loop(
    session_factory: async_sessionmaker[AsyncSession],
    telegram_client: TelegramClientProtocol,
    chat_id: int,
    llm_service: LLMService | None,
    settings: Settings,
    interval_seconds: int = 30,
) -> None:
    """Poll connected Gmail accounts; never create a task automatically."""

    if not settings.gmail_enabled:
        logger.info("gmail_polling_disabled")
        return
    if not settings.gmail_client_id or not settings.gmail_client_secret:
        logger.warning("gmail_polling_missing_oauth_credentials")
        return
    if llm_service is None:
        logger.warning("gmail_polling_missing_llm_service")
        return
    try:
        cipher = TokenCipher(settings.token_encryption_key)
    except TokenEncryptionError:
        logger.exception("gmail_polling_invalid_token_key")
        return

    oauth = GmailOAuthClient(
        settings.gmail_client_id,
        settings.gmail_client_secret,
        settings.gmail_redirect_uri,
    )
    try:
        while True:
            try:
                await poll_gmail_accounts(
                    session_factory,
                    telegram_client,
                    chat_id,
                    llm_service,
                    settings,
                    cipher,
                    oauth,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("gmail_polling_failed")
            await asyncio.sleep(interval_seconds)
    finally:
        await oauth.close()


async def poll_gmail_accounts(
    session_factory: async_sessionmaker[AsyncSession],
    telegram_client: TelegramClientProtocol,
    chat_id: int,
    llm_service: LLMService,
    settings: Settings,
    cipher: TokenCipher,
    oauth: GmailOAuthClient,
    now: datetime | None = None,
) -> int:
    current = now or datetime.now(UTC)
    async with session_factory() as session:
        accounts = list(
            (
                await session.scalars(
                    select(IntegrationAccount).where(
                        IntegrationAccount.provider == IntegrationProvider.GMAIL,
                        IntegrationAccount.status == IntegrationStatus.CONNECTED,
                    )
                )
            ).all()
        )
        account_ids = [account.id for account in accounts]

    processed = 0
    inbox = GmailInboxService(session_factory)
    for account_id in account_ids:
        account = await _load_account(session_factory, account_id)
        if account is None:
            continue
        user_settings = await _load_settings(session_factory, account.user_id)
        poll_minutes = user_settings.gmail_poll_minutes if user_settings else 15
        if account.last_polled_at is not None and account.last_polled_at > current - timedelta(
            minutes=poll_minutes
        ):
            continue
        try:
            access_token = await _access_token(
                session_factory, account, cipher, oauth, current
            )
            gmail = GmailAPIClient(access_token)
            try:
                result = await inbox.sync_account(
                    account,
                    gmail,
                    llm_service,
                    lambda source_id, candidate: notify_gmail_candidate(
                        telegram_client,
                        chat_id,
                        source_id,
                        candidate,
                        settings.timezone,
                    ),
                    settings.gmail_query,
                    current,
                    settings.gmail_start_at,
                )
                processed += result.candidates
            finally:
                await gmail.close()
        except Exception as exc:
            await _mark_account_error(session_factory, account.id, str(exc)[:2000])
            logger.exception("gmail_account_poll_failed", extra={"source_id": str(account.id)})
    return processed


async def _access_token(
    session_factory: async_sessionmaker[AsyncSession],
    account: IntegrationAccount,
    cipher: TokenCipher,
    oauth: GmailOAuthClient,
    now: datetime,
) -> str:
    if account.token_expires_at is None or account.token_expires_at > now + timedelta(minutes=1):
        return cipher.decrypt(account.access_token_encrypted)
    if not account.refresh_token_encrypted:
        raise TokenEncryptionError("Gmail access token expired and no refresh token is stored")
    refresh_token = cipher.decrypt(account.refresh_token_encrypted)
    token = await oauth.refresh(refresh_token)
    async with session_factory() as session:
        current = await session.get(IntegrationAccount, account.id)
        if current is None:
            raise ValueError("Gmail integration account not found")
        await save_gmail_oauth_token(session, current.user_id, token, cipher)
        await session.commit()
    return token.access_token


async def _load_account(
    session_factory: async_sessionmaker[AsyncSession], account_id: object
) -> IntegrationAccount | None:
    async with session_factory() as session:
        account = await session.get(IntegrationAccount, account_id)
        if account is None:
            return None
        session.expunge(account)
        return account


async def _load_settings(
    session_factory: async_sessionmaker[AsyncSession], user_id: object
) -> UserSettings | None:
    async with session_factory() as session:
        settings = await session.get(UserSettings, user_id)
        if settings is None:
            return None
        session.expunge(settings)
        return settings


async def _mark_account_error(
    session_factory: async_sessionmaker[AsyncSession], account_id: object, message: str
) -> None:
    async with session_factory() as session:
        account = await session.get(IntegrationAccount, account_id)
        if account is not None:
            account.status = IntegrationStatus.ERROR
            account.error_message = message
            await session.commit()
