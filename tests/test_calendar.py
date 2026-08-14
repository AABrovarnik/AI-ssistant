from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import pytest
from app.db.session import session_factory
from app.integrations.calendar.client import (
    CALENDAR_SCOPE,
    CalendarEvent,
    CalendarOAuthClient,
    calendar_event_payload,
)
from app.integrations.calendar.service import (
    CalendarEventWindow,
    CalendarTaskService,
    resolve_event_window,
)
from app.integrations.gmail.security import TokenCipher
from app.tasks.schemas import TaskCreate
from app.tasks.service import TaskService
from cryptography.fernet import Fernet


class FakeCalendarClient:
    def __init__(self) -> None:
        self.created: list[CalendarEventWindow] = []
        self.updated: list[tuple[str, CalendarEventWindow]] = []
        self.deleted: list[str] = []

    async def create_event(self, summary, description, start_at, end_at) -> CalendarEvent:
        self.created.append(CalendarEventWindow(start_at, end_at))
        return CalendarEvent("event-1", "https://calendar.google.com/event-1")

    async def update_event(self, event_id, summary, description, start_at, end_at) -> CalendarEvent:
        self.updated.append((event_id, CalendarEventWindow(start_at, end_at)))
        return CalendarEvent(event_id, "https://calendar.google.com/event-1")

    async def delete_event(self, event_id) -> None:
        self.deleted.append(event_id)


@pytest.mark.asyncio
async def test_calendar_oauth_url_uses_calendar_events_scope() -> None:
    client = CalendarOAuthClient("client", "secret", "http://localhost/callback")
    try:
        query = parse_qs(urlparse(client.authorization_url("state-1")).query)
    finally:
        await client.close()

    assert query["scope"] == [CALENDAR_SCOPE]
    assert query["redirect_uri"] == ["http://localhost/callback"]


def test_calendar_event_payload_is_explicit_and_utc() -> None:
    payload = calendar_event_payload(
        "Review contract",
        "Bring the latest version",
        datetime(2026, 8, 14, 10, 0, tzinfo=UTC),
        datetime(2026, 8, 14, 10, 30, tzinfo=UTC),
    )

    assert payload == {
        "summary": "Review contract",
        "description": "Bring the latest version",
        "start": {"dateTime": "2026-08-14T10:00:00Z", "timeZone": "UTC"},
        "end": {"dateTime": "2026-08-14T10:30:00Z", "timeZone": "UTC"},
    }


def test_resolve_event_window_uses_task_deadline_as_end() -> None:
    class TaskWithTimes:
        start_at = None
        due_at = datetime(2026, 8, 14, 10, 0, tzinfo=UTC)

    window = resolve_event_window(TaskWithTimes(), None, None)

    assert window.start_at == datetime(2026, 8, 14, 9, 30, tzinfo=UTC)
    assert window.end_at == datetime(2026, 8, 14, 10, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_calendar_event_link_is_idempotent_and_updatable() -> None:
    async with session_factory() as session:
        task = await TaskService(session).create(
            TaskCreate(
                title="Review contract",
                due_at=datetime(2026, 8, 14, 10, 0, tzinfo=UTC),
                idempotency_key="calendar-task-1",
            )
        )
        client = FakeCalendarClient()
        oauth = CalendarOAuthClient("client", "secret", "http://localhost/callback")
        service = CalendarTaskService(
            session,
            TokenCipher(Fernet.generate_key().decode()),
            oauth,
        )
        try:
            window = resolve_event_window(task, None, None)
            created = await service.create_event(task.id, window, client)
            repeated = await service.create_event(task.id, window, client)
            updated = await service.update_event(
                task.id,
                CalendarEventWindow(
                    datetime(2026, 8, 14, 11, 0, tzinfo=UTC),
                    datetime(2026, 8, 14, 11, 30, tzinfo=UTC),
                ),
                client,
            )
            deleted = await service.delete_event(task.id, client)
        finally:
            await oauth.close()

    assert created.event_id == "event-1"
    assert repeated.event_id == "event-1"
    assert updated.status == "SYNCED"
    assert deleted.status == "UNLINKED"
    assert len(client.created) == 1
    assert client.updated[0][0] == "event-1"
    assert client.deleted == ["event-1"]
