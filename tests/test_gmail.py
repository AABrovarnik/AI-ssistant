from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest
from app.db.session import session_factory
from app.integrations.gmail.client import GmailMessage
from app.integrations.gmail.oauth_state import OAuthStateError, create_state, verify_state
from app.integrations.gmail.service import (
    GmailFilterDecision,
    GmailInboxService,
    gmail_filter_decision,
)
from app.llm.schemas import (
    ClassificationResult,
    MessageClassification,
    ParsedMessage,
    TaskCandidate,
    TaskExtractionResult,
)
from app.llm.service import LLMService
from app.tasks.models import IntegrationAccount, IntegrationProvider, UserSettings
from app.tasks.service import TaskService


def test_gmail_oauth_state_is_signed_and_short_lived() -> None:
    state = create_state("user-1", "test-key", now=100)
    assert verify_state(state, "test-key", now=200) == "user-1"
    with pytest.raises(OAuthStateError):
        verify_state(state, "wrong-key", now=200)
    with pytest.raises(OAuthStateError):
        verify_state(state, "test-key", now=701)


class FakeGmailClient:
    def __init__(self, message: GmailMessage) -> None:
        self.message = message
        self.list_queries: list[str] = []
        self.get_calls = 0

    async def list_message_ids(self, query: str) -> list[str]:
        self.list_queries.append(query)
        return [self.message.message_id]

    async def get_message(self, message_id: str) -> GmailMessage:
        assert message_id == self.message.message_id
        self.get_calls += 1
        return self.message


class FakeLLM:
    async def parse_message(
        self, text: str, current_datetime: datetime | None = None
    ) -> ParsedMessage:
        assert "направить документы" in text
        return ParsedMessage(
            classification=ClassificationResult(
                classification=MessageClassification.TASK,
                confidence=0.96,
            ),
            extraction=TaskExtractionResult(
                task_detected=True,
                candidate=TaskCandidate(
                    task_type="MY_TASK",
                    title="Направить документы",
                    due_date="2026-08-14",
                    confidence=0.91,
                    evidence="Просим направить документы не позднее 14 августа",
                ),
            ),
        )


def gmail_message() -> GmailMessage:
    return GmailMessage(
        message_id="gmail-message-1",
        thread_id="gmail-thread-1",
        sender_email="client@example.com",
        sender_name="Client",
        subject="Документы",
        text="Просим направить документы не позднее 14 августа",
        received_at=datetime(2026, 8, 12, 12, 0, tzinfo=UTC),
        source_url="https://mail.google.com/mail/u/0/#inbox/gmail-message-1",
        list_unsubscribe=False,
        headers={"message-id": "<gmail-message-1@example.com>"},
    )


def test_gmail_filter_defaults_to_normal_and_ignores_newsletters() -> None:
    normal = UserSettings(user_id=UUID(int=1))
    assert gmail_filter_decision(gmail_message(), normal) == GmailFilterDecision.NORMAL
    newsletter = gmail_message()
    newsletter = GmailMessage(**{**newsletter.__dict__, "list_unsubscribe": True})
    assert gmail_filter_decision(newsletter, normal) == GmailFilterDecision.IGNORE


@pytest.mark.asyncio
async def test_gmail_poll_stores_candidate_and_is_idempotent() -> None:
    async with session_factory() as session:
        user = await TaskService(session).ensure_user()
        account = IntegrationAccount(
            user_id=user.id,
            provider=IntegrationProvider.GMAIL,
            access_token_encrypted="encrypted",
        )
        session.add(account)
        settings = await session.get(UserSettings, user.id)
        assert settings is not None
        await session.commit()
        await session.refresh(account)

    client = FakeGmailClient(gmail_message())
    notifications: list[tuple[UUID, TaskCandidate]] = []

    async def notify(source_id: UUID, candidate: TaskCandidate) -> None:
        notifications.append((source_id, candidate))

    inbox = GmailInboxService(session_factory)
    first = await inbox.sync_account(
        account,
        cast(Any, client),
        cast(LLMService, FakeLLM()),
        notify,
        now=datetime(2026, 8, 12, 13, 0, tzinfo=UTC),
    )
    second = await inbox.sync_account(
        account,
        cast(Any, client),
        cast(LLMService, FakeLLM()),
        notify,
        now=datetime(2026, 8, 12, 13, 15, tzinfo=UTC),
    )

    assert first.fetched == 1
    assert first.stored == 1
    assert first.candidates == 1
    assert second.duplicates == 1
    assert len(notifications) == 1
    assert notifications[0][1].title == "Направить документы"
    assert client.list_queries == [
        "in:anywhere -label:spam",
        "in:anywhere -label:spam after:1786539600",
    ]

    async with session_factory() as session:
        service = TaskService(session)
        source = await service.find_source_message("GMAIL", "gmail-message-1")
        assert source is not None
        assert source.processing_status == "PROCESSED"
        assert source.extra["candidate"]["title"] == "Направить документы"
