"""Small Google Calendar REST and OAuth clients."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"
CALENDAR_API_BASE_URL = "https://www.googleapis.com/calendar/v3"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


class CalendarAPIError(RuntimeError):
    """Safe-to-log Calendar API error without credentials or request URLs."""


class CalendarOAuthError(CalendarAPIError):
    """Google OAuth authorization or refresh failed."""


@dataclass(frozen=True)
class CalendarOAuthToken:
    access_token: str
    refresh_token: str | None
    expires_in: int | None
    scope: str


@dataclass(frozen=True)
class CalendarEvent:
    event_id: str
    html_link: str | None = None


class CalendarOAuthClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=10.0)
        )

    def authorization_url(self, state: str) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "access_type": "offline",
            "prompt": "consent",
            "scope": CALENDAR_SCOPE,
            "state": state,
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> CalendarOAuthToken:
        response = await self._http.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": self.redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        return self._parse_token_response(response)

    async def refresh(self, refresh_token: str) -> CalendarOAuthToken:
        response = await self._http.post(
            GOOGLE_TOKEN_URL,
            data={
                "refresh_token": refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
            },
        )
        return self._parse_token_response(response, refresh_token=refresh_token)

    @staticmethod
    def _parse_token_response(
        response: httpx.Response, refresh_token: str | None = None
    ) -> CalendarOAuthToken:
        if response.is_error:
            raise CalendarOAuthError("Google OAuth token request failed")
        payload = response.json()
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise CalendarOAuthError("Google OAuth response has no access token")
        expires_in = payload.get("expires_in")
        return CalendarOAuthToken(
            access_token=access_token,
            refresh_token=payload.get("refresh_token") or refresh_token,
            expires_in=expires_in if isinstance(expires_in, int) else None,
            scope=str(payload.get("scope", CALENDAR_SCOPE)),
        )

    async def close(self) -> None:
        await self._http.aclose()


class CalendarClientProtocol(Protocol):
    async def create_event(
        self,
        summary: str,
        description: str | None,
        start_at: datetime,
        end_at: datetime,
    ) -> CalendarEvent: ...

    async def update_event(
        self,
        event_id: str,
        summary: str,
        description: str | None,
        start_at: datetime,
        end_at: datetime,
    ) -> CalendarEvent: ...

    async def delete_event(self, event_id: str) -> None: ...


class CalendarAPIClient:
    def __init__(self, access_token: str, http_client: httpx.AsyncClient | None = None) -> None:
        self._http = http_client or httpx.AsyncClient(
            base_url=CALENDAR_API_BASE_URL,
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"Authorization": f"Bearer {access_token}"},
        )

    async def create_event(
        self,
        summary: str,
        description: str | None,
        start_at: datetime,
        end_at: datetime,
    ) -> CalendarEvent:
        response = await self._http.post(
            "/calendars/primary/events",
            json=calendar_event_payload(summary, description, start_at, end_at),
        )
        return self._parse_event(self._parse_response(response))

    async def update_event(
        self,
        event_id: str,
        summary: str,
        description: str | None,
        start_at: datetime,
        end_at: datetime,
    ) -> CalendarEvent:
        response = await self._http.patch(
            f"/calendars/primary/events/{event_id}",
            json=calendar_event_payload(summary, description, start_at, end_at),
        )
        return self._parse_event(self._parse_response(response))

    async def delete_event(self, event_id: str) -> None:
        response = await self._http.delete(f"/calendars/primary/events/{event_id}")
        if response.status_code in {401, 403}:
            raise CalendarAPIError("Google Calendar authentication failed")
        if response.status_code not in {200, 204}:
            raise CalendarAPIError(f"Google Calendar API HTTP error {response.status_code}")

    @staticmethod
    def _parse_response(response: httpx.Response) -> dict[str, Any]:
        if response.status_code in {401, 403}:
            raise CalendarAPIError("Google Calendar authentication failed")
        if response.is_error:
            raise CalendarAPIError(f"Google Calendar API HTTP error {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise CalendarAPIError("Google Calendar API returned an invalid payload")
        return payload

    @staticmethod
    def _parse_event(payload: dict[str, Any]) -> CalendarEvent:
        event_id = payload.get("id")
        if not isinstance(event_id, str) or not event_id:
            raise CalendarAPIError("Google Calendar response has no event id")
        html_link = payload.get("htmlLink")
        return CalendarEvent(event_id, html_link if isinstance(html_link, str) else None)

    async def close(self) -> None:
        await self._http.aclose()


def calendar_event_payload(
    summary: str,
    description: str | None,
    start_at: datetime,
    end_at: datetime,
) -> dict[str, object]:
    return {
        "summary": summary,
        "description": description or "",
        "start": {"dateTime": _iso_utc(start_at), "timeZone": "UTC"},
        "end": {"dateTime": _iso_utc(end_at), "timeZone": "UTC"},
    }


def _iso_utc(value: datetime) -> str:
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.isoformat().replace("+00:00", "Z")
