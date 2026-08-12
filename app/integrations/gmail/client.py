"""Small Gmail REST and OAuth clients with no write scopes."""

from __future__ import annotations

import base64
import html
import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


class GmailAPIError(RuntimeError):
    """Safe-to-log Gmail API error without credentials or request URLs."""


class GmailOAuthError(GmailAPIError):
    """OAuth authorization or refresh failed."""


@dataclass(frozen=True)
class GmailOAuthToken:
    access_token: str
    refresh_token: str | None
    expires_in: int | None
    scope: str


class GmailOAuthClient:
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
        self._http = http_client or httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0))

    def authorization_url(self, state: str) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "access_type": "offline",
            "prompt": "consent",
            "scope": GMAIL_READONLY_SCOPE,
            "state": state,
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> GmailOAuthToken:
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

    async def refresh(self, refresh_token: str) -> GmailOAuthToken:
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
    ) -> GmailOAuthToken:
        if response.is_error:
            raise GmailOAuthError("Google OAuth token request failed")
        payload = response.json()
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise GmailOAuthError("Google OAuth response has no access token")
        expires_in = payload.get("expires_in")
        return GmailOAuthToken(
            access_token=access_token,
            refresh_token=payload.get("refresh_token") or refresh_token,
            expires_in=expires_in if isinstance(expires_in, int) else None,
            scope=str(payload.get("scope", GMAIL_READONLY_SCOPE)),
        )

    async def close(self) -> None:
        await self._http.aclose()


@dataclass(frozen=True)
class GmailMessage:
    message_id: str
    thread_id: str | None
    sender_email: str | None
    sender_name: str | None
    subject: str | None
    text: str
    received_at: datetime | None
    source_url: str
    list_unsubscribe: bool
    headers: dict[str, str]


class GmailClientProtocol(Protocol):
    async def list_message_ids(self, query: str) -> list[str]: ...

    async def get_message(self, message_id: str) -> GmailMessage: ...


class GmailAPIClient:
    def __init__(self, access_token: str, http_client: httpx.AsyncClient | None = None) -> None:
        self._http = http_client or httpx.AsyncClient(
            base_url="https://gmail.googleapis.com/gmail/v1",
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"Authorization": f"Bearer {access_token}"},
        )

    async def list_message_ids(self, query: str) -> list[str]:
        message_ids: list[str] = []
        page_token: str | None = None
        while True:
            response = await self._http.get(
                "/users/me/messages",
                params={"q": query, "pageToken": page_token, "maxResults": 100},
            )
            payload = self._parse_response(response)
            for item in payload.get("messages", []):
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    message_ids.append(item["id"])
            page_token = payload.get("nextPageToken")
            if not isinstance(page_token, str) or not page_token:
                return message_ids

    async def get_message(self, message_id: str) -> GmailMessage:
        response = await self._http.get(
            f"/users/me/messages/{message_id}", params={"format": "full"}
        )
        return self._parse_message(self._parse_response(response))

    @staticmethod
    def _parse_response(response: httpx.Response) -> dict[str, Any]:
        if response.status_code in {401, 403}:
            raise GmailAPIError("Gmail authentication failed")
        if response.is_error:
            raise GmailAPIError(f"Gmail API HTTP error {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise GmailAPIError("Gmail API returned an invalid payload")
        return payload

    @classmethod
    def _parse_message(cls, payload: dict[str, Any]) -> GmailMessage:
        message_id = str(payload.get("id", ""))
        if not message_id:
            raise GmailAPIError("Gmail message has no id")
        headers = cls._headers(payload.get("payload"))
        sender_email, sender_name = cls._parse_sender(headers.get("from"))
        received_at = cls._parse_date(headers.get("date"))
        return GmailMessage(
            message_id=message_id,
            thread_id=payload.get("threadId"),
            sender_email=sender_email,
            sender_name=sender_name,
            subject=headers.get("subject"),
            text=cls._body(payload.get("payload")),
            received_at=received_at,
            source_url=f"https://mail.google.com/mail/u/0/#inbox/{message_id}",
            list_unsubscribe="list-unsubscribe" in headers,
            headers=headers,
        )

    @staticmethod
    def _headers(payload: Any) -> dict[str, str]:
        if not isinstance(payload, dict):
            return {}
        result: dict[str, str] = {}
        for item in payload.get("headers", []):
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            value = item.get("value")
            if isinstance(name, str) and isinstance(value, str):
                result[name.casefold()] = value
        return result

    @classmethod
    def _body(cls, payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        mime_type = payload.get("mimeType")
        body = payload.get("body")
        if mime_type == "text/plain" and isinstance(body, dict):
            data = body.get("data")
            if isinstance(data, str):
                return cls._decode(data)
        parts = payload.get("parts")
        if isinstance(parts, list):
            plain_parts = [
                cls._body(part) for part in parts if cls._mime_type(part) == "text/plain"
            ]
            if plain_parts:
                return "\n".join(part for part in plain_parts if part)
            html_parts = [cls._body(part) for part in parts if cls._mime_type(part) == "text/html"]
            if html_parts:
                return cls._strip_html("\n".join(html_parts))
        if (
            mime_type == "text/html"
            and isinstance(body, dict)
            and isinstance(body.get("data"), str)
        ):
            return cls._strip_html(cls._decode(body["data"]))
        return ""

    @staticmethod
    def _mime_type(payload: Any) -> str:
        return str(payload.get("mimeType", "")) if isinstance(payload, dict) else ""

    @staticmethod
    def _decode(value: str) -> str:
        try:
            return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode(
                "utf-8", errors="replace"
            )
        except ValueError:
            return ""

    @staticmethod
    def _strip_html(value: str) -> str:
        return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()

    @staticmethod
    def _parse_sender(value: str | None) -> tuple[str | None, str | None]:
        if not value:
            return None, None
        match = re.search(r"<([^>]+)>", value)
        if match:
            email = match.group(1).strip().casefold()
            name = value[: match.start()].strip().strip('"') or None
            return email, name
        return value.strip().casefold(), None

    @staticmethod
    def _parse_date(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None

    async def close(self) -> None:
        await self._http.aclose()
