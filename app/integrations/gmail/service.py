"""Gmail inbox polling, filtering and candidate creation."""

from __future__ import annotations

import fnmatch
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.integrations.gmail.client import GmailClientProtocol, GmailMessage
from app.llm.schemas import MessageClassification, TaskCandidate
from app.llm.service import LLMParseError, LLMService
from app.tasks.models import IntegrationAccount, ProcessingStatus, UserSettings
from app.tasks.schemas import SourceMessageCreate
from app.tasks.service import DuplicateSourceError, TaskService

if TYPE_CHECKING:
    from app.integrations.telegram.client import TelegramClientProtocol


class GmailFilterDecision(StrEnum):
    VIP = "VIP"
    TRUSTED = "TRUSTED"
    NORMAL = "NORMAL"
    IGNORE = "IGNORE"


@dataclass(frozen=True)
class GmailSyncResult:
    fetched: int = 0
    stored: int = 0
    duplicates: int = 0
    ignored: int = 0
    candidates: int = 0
    failed: int = 0


CandidateNotifier = Callable[[UUID, TaskCandidate], Awaitable[None]]
DEFAULT_GMAIL_START_AT = datetime(2026, 8, 1, tzinfo=UTC)


def _patterns(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item).strip().casefold() for item in value if str(item).strip()}


def _matches(sender: str | None, patterns: set[str]) -> bool:
    if not sender:
        return False
    sender = sender.casefold()
    domain = sender.rsplit("@", 1)[-1] if "@" in sender else ""
    return any(
        fnmatch.fnmatch(sender, pattern) or fnmatch.fnmatch(domain, pattern.lstrip("@"))
        for pattern in patterns
    )


def gmail_filter_decision(
    message: GmailMessage,
    settings: UserSettings,
) -> GmailFilterDecision:
    config = (settings.extra or {}).get("gmail")
    gmail_config = config if isinstance(config, dict) else {}
    blocked = _patterns(gmail_config.get("sender_blocklist"))
    allowed = _patterns(gmail_config.get("sender_allowlist"))
    vip = _patterns(gmail_config.get("vip_senders"))
    ignore_newsletters = gmail_config.get("ignore_newsletters", True) is not False

    if _matches(message.sender_email, blocked):
        return GmailFilterDecision.IGNORE
    if _matches(message.sender_email, vip):
        return GmailFilterDecision.VIP
    if _matches(message.sender_email, allowed):
        return GmailFilterDecision.TRUSTED
    if message.list_unsubscribe and ignore_newsletters:
        return GmailFilterDecision.IGNORE
    return GmailFilterDecision.NORMAL


class GmailInboxService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def sync_account(
        self,
        account: IntegrationAccount,
        client: GmailClientProtocol,
        llm_service: LLMService | None,
        notify_candidate: CandidateNotifier | None = None,
        query: str = "in:anywhere -label:spam",
        now: datetime | None = None,
        minimum_received_at: datetime | None = DEFAULT_GMAIL_START_AT,
    ) -> GmailSyncResult:
        current = now or datetime.now(UTC)
        poll_query = self._poll_query(query, account.last_polled_at, minimum_received_at)
        message_ids = await client.list_message_ids(poll_query)
        result = GmailSyncResult(fetched=len(message_ids))

        async with self.session_factory() as session:
            current_account = await session.get(IntegrationAccount, account.id)
            if current_account is None:
                raise ValueError("Gmail integration account not found")
            user_settings = await session.get(UserSettings, current_account.user_id)
            if user_settings is None:
                user_settings = UserSettings(user_id=current_account.user_id)
                session.add(user_settings)
                await session.flush()
            service = TaskService(session)
            for message_id in message_ids:
                existing = await service.find_source_message(
                    "GMAIL", message_id, current_account.user_id
                )
                if existing is not None and existing.processing_status in {
                    ProcessingStatus.PROCESSED,
                    ProcessingStatus.IGNORED,
                }:
                    result = self._increment(result, "duplicates")
                    continue
                try:
                    message = await client.get_message(message_id)
                    source = existing
                    if source is None:
                        source = await service.create_source_message(
                            SourceMessageCreate(
                                source_type="GMAIL",
                                external_id=message.message_id,
                                sender_external_id=message.sender_email,
                                sender_name=message.sender_name,
                                sender_email=message.sender_email,
                                subject=message.subject,
                                text=message.text,
                                received_at=message.received_at,
                                thread_id=message.thread_id,
                                source_url=message.source_url,
                            ),
                            current_account.user_id,
                        )
                        result = self._increment(result, "stored")
                    decision = gmail_filter_decision(message, user_settings)
                    source.extra = {
                        **source.extra,
                        "gmail": {
                            "thread_id": message.thread_id,
                            "filter_decision": decision.value,
                            "headers": {
                                key: value
                                for key, value in message.headers.items()
                                if key in {"message-id", "list-unsubscribe", "reply-to"}
                            },
                        },
                    }
                    if decision == GmailFilterDecision.IGNORE:
                        source.processing_status = ProcessingStatus.IGNORED
                        source.error_code = "GMAIL_FILTERED"
                        result = self._increment(result, "ignored")
                        continue
                    if llm_service is None:
                        source.processing_status = ProcessingStatus.FAILED
                        source.error_code = "LLM_UNAVAILABLE"
                        result = self._increment(result, "failed")
                        continue
                    parsed = await llm_service.parse_message(
                        self._llm_text(message), current_datetime=current
                    )
                    candidate = parsed.extraction.candidate if parsed.extraction else None
                    if (
                        candidate is None
                        or parsed.classification.classification
                        not in {
                            MessageClassification.TASK,
                            MessageClassification.DELEGATION,
                            MessageClassification.AWAITING,
                        }
                    ):
                        source.processing_status = ProcessingStatus.PROCESSED
                        source.classification = parsed.classification.classification.value
                        source.confidence = parsed.classification.confidence
                        result = self._increment(result, "ignored")
                        continue
                    source.processing_status = ProcessingStatus.PROCESSED
                    source.classification = parsed.classification.classification.value
                    source.confidence = parsed.classification.confidence
                    source.extra = {
                        **source.extra,
                        "candidate": candidate.model_dump(mode="json"),
                    }
                    result = self._increment(result, "candidates")
                    if notify_candidate is not None:
                        await notify_candidate(source.id, candidate)
                except DuplicateSourceError:
                    result = self._increment(result, "duplicates")
                except (LLMParseError, ValueError):
                    if existing is not None:
                        existing.processing_status = ProcessingStatus.FAILED
                        existing.error_code = "GMAIL_PROCESSING_FAILED"
                    result = self._increment(result, "failed")
            current_account.last_polled_at = current
            current_account.error_message = None
            await session.commit()
        account.last_polled_at = current
        account.error_message = None
        return result

    @staticmethod
    def _poll_query(
        query: str,
        last_polled_at: datetime | None,
        minimum_received_at: datetime | None = DEFAULT_GMAIL_START_AT,
    ) -> str:
        poll_from = last_polled_at
        if minimum_received_at is not None and (
            poll_from is None or poll_from < minimum_received_at
        ):
            poll_from = minimum_received_at
        if poll_from is None:
            return query
        timestamp = int(poll_from.astimezone(UTC).timestamp())
        return f"{query} after:{timestamp}"

    @staticmethod
    def _llm_text(message: GmailMessage) -> str:
        if message.subject and message.text:
            return f"Тема письма: {message.subject}\n\n{message.text}"
        return message.text or message.subject or "Пустое письмо"

    @staticmethod
    def _increment(result: GmailSyncResult, field: str) -> GmailSyncResult:
        return replace(result, **{field: getattr(result, field) + 1})


async def notify_gmail_candidate(
    client: TelegramClientProtocol,
    chat_id: int,
    source_id: UUID,
    candidate: TaskCandidate,
    timezone: str,
) -> None:
    """Send a candidate preview; task creation remains a Telegram callback."""

    from app.integrations.telegram.bot import TelegramBot

    await client.send_message(
        chat_id,
        TelegramBot._format_candidate(candidate, timezone),
        TelegramBot._candidate_keyboard(source_id),
    )
