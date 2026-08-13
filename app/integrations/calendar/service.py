"""Explicit task-to-Google-Calendar synchronization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.calendar.accounts import save_calendar_oauth_token
from app.integrations.calendar.client import (
    CalendarAPIClient,
    CalendarAPIError,
    CalendarClientProtocol,
    CalendarEvent,
    CalendarOAuthClient,
)
from app.integrations.gmail.security import TokenCipher
from app.tasks.models import IntegrationAccount, IntegrationProvider, IntegrationStatus, Task
from app.tasks.service import TaskService


class CalendarNotConnectedError(RuntimeError):
    """The user has not connected Google Calendar."""


class CalendarSyncError(RuntimeError):
    """The task cannot be represented as a Calendar event."""


@dataclass(frozen=True)
class CalendarEventWindow:
    start_at: datetime
    end_at: datetime


@dataclass(frozen=True)
class CalendarTaskResult:
    task_id: UUID
    event_id: str | None
    html_link: str | None
    status: str


class CalendarTaskService:
    def __init__(
        self,
        session: AsyncSession,
        cipher: TokenCipher,
        oauth: CalendarOAuthClient,
    ) -> None:
        self.session = session
        self.cipher = cipher
        self.oauth = oauth

    async def create_event(
        self,
        task_id: UUID,
        window: CalendarEventWindow,
        client: CalendarClientProtocol | None = None,
    ) -> CalendarTaskResult:
        task = await TaskService(self.session).get(task_id)
        if task.calendar_event_id:
            return CalendarTaskResult(
                task.id, task.calendar_event_id, None, task.calendar_sync_status or "SYNCED"
            )
        calendar_client, should_close = await self._client(client, task.user_id)
        try:
            event = await calendar_client.create_event(
                task.title, task.description, window.start_at, window.end_at
            )
        except CalendarAPIError:
            task.calendar_sync_status = "ERROR"
            await self.session.commit()
            raise
        finally:
            if should_close:
                await calendar_client.close()  # type: ignore[attr-defined]
        return await self._save_event(task, event, window)

    async def update_event(
        self,
        task_id: UUID,
        window: CalendarEventWindow,
        client: CalendarClientProtocol | None = None,
    ) -> CalendarTaskResult:
        task = await TaskService(self.session).get(task_id)
        if not task.calendar_event_id:
            raise CalendarSyncError("task has no linked calendar event")
        calendar_client, should_close = await self._client(client, task.user_id)
        try:
            event = await calendar_client.update_event(
                task.calendar_event_id,
                task.title,
                task.description,
                window.start_at,
                window.end_at,
            )
        except CalendarAPIError:
            task.calendar_sync_status = "ERROR"
            await self.session.commit()
            raise
        finally:
            if should_close:
                await calendar_client.close()  # type: ignore[attr-defined]
        return await self._save_event(task, event, window)

    async def delete_event(
        self,
        task_id: UUID,
        client: CalendarClientProtocol | None = None,
    ) -> CalendarTaskResult:
        task = await TaskService(self.session).get(task_id)
        if not task.calendar_event_id:
            return CalendarTaskResult(task.id, None, None, "UNLINKED")
        calendar_client, should_close = await self._client(client, task.user_id)
        try:
            await calendar_client.delete_event(task.calendar_event_id)
        except CalendarAPIError:
            task.calendar_sync_status = "ERROR"
            await self.session.commit()
            raise
        finally:
            if should_close:
                await calendar_client.close()  # type: ignore[attr-defined]
        task.calendar_event_id = None
        task.calendar_sync_status = "UNLINKED"
        await self.session.commit()
        return CalendarTaskResult(task.id, None, None, "UNLINKED")

    async def _client(
        self,
        client: CalendarClientProtocol | None,
        user_id: UUID,
    ) -> tuple[CalendarClientProtocol, bool]:
        if client is not None:
            return client, False
        account = await self.session.scalar(
            select(IntegrationAccount).where(
                IntegrationAccount.user_id == user_id,
                IntegrationAccount.provider == IntegrationProvider.GOOGLE_CALENDAR,
                IntegrationAccount.status == IntegrationStatus.CONNECTED,
            )
        )
        if account is None:
            raise CalendarNotConnectedError("Google Calendar is not connected")
        token = await self._access_token(account)
        return CalendarAPIClient(token), True

    async def _access_token(self, account: IntegrationAccount) -> str:
        now = datetime.now(UTC)
        if account.token_expires_at is None or account.token_expires_at > now + timedelta(
            minutes=1
        ):
            return self.cipher.decrypt(account.access_token_encrypted)
        if not account.refresh_token_encrypted:
            raise CalendarSyncError("Calendar access token expired and no refresh token is stored")
        token = await self.oauth.refresh(self.cipher.decrypt(account.refresh_token_encrypted))
        current = await self.session.get(IntegrationAccount, account.id)
        if current is None:
            raise CalendarNotConnectedError("Google Calendar account not found")
        await save_calendar_oauth_token(self.session, current.user_id, token, self.cipher)
        return token.access_token

    async def _save_event(
        self, task: Task, event: CalendarEvent, window: CalendarEventWindow
    ) -> CalendarTaskResult:
        task.calendar_event_id = event.event_id
        task.calendar_sync_status = "SYNCED"
        task.extra = {
            **task.extra,
            "calendar": {
                "start_at": window.start_at.isoformat(),
                "end_at": window.end_at.isoformat(),
            },
        }
        await self.session.commit()
        return CalendarTaskResult(task.id, event.event_id, event.html_link, "SYNCED")


def resolve_event_window(
    task: Task,
    start_at: datetime | None,
    end_at: datetime | None,
) -> CalendarEventWindow:
    resolved_end = end_at or task.due_at
    resolved_start = start_at or task.start_at
    stored_window = getattr(task, "extra", {}).get("calendar", {})
    if isinstance(stored_window, dict):
        if resolved_start is None and isinstance(stored_window.get("start_at"), str):
            resolved_start = datetime.fromisoformat(stored_window["start_at"])
        if resolved_end is None and isinstance(stored_window.get("end_at"), str):
            resolved_end = datetime.fromisoformat(stored_window["end_at"])
    if resolved_start is None and resolved_end is not None:
        resolved_start = resolved_end - timedelta(minutes=30)
    if resolved_start is None or resolved_end is None:
        raise CalendarSyncError("calendar event requires task start_at and due_at")
    if resolved_end <= resolved_start:
        raise CalendarSyncError("calendar event end_at must be after start_at")
    return CalendarEventWindow(
        _as_utc(resolved_start),
        _as_utc(resolved_end),
    )


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
