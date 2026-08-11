from collections.abc import Mapping
from typing import Any, Protocol

import httpx


class TelegramClientProtocol(Protocol):
    async def get_updates(
        self, offset: int | None = None, timeout: int = 25
    ) -> list[dict[str, Any]]: ...

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    async def answer_callback_query(self, callback_query_id: str, text: str) -> None: ...

    async def close(self) -> None: ...


class TelegramAPIError(RuntimeError):
    """Safe-to-log Telegram API error without request URLs or credentials."""


class TelegramAuthenticationError(TelegramAPIError):
    """The configured bot token was rejected by Telegram."""


class TelegramClient:
    def __init__(self, token: str, http_client: httpx.AsyncClient | None = None) -> None:
        self._http = http_client or httpx.AsyncClient(
            base_url=f"https://api.telegram.org/bot{token}",
            timeout=httpx.Timeout(35.0, connect=10.0),
        )

    async def _call(self, method: str, **params: Any) -> Any:
        params = {key: value for key, value in params.items() if value is not None}
        response = await self._http.post(f"/{method}", json=params)
        if response.status_code == 401:
            raise TelegramAuthenticationError("Telegram bot authentication failed")
        if response.is_error:
            raise TelegramAPIError(f"Telegram API HTTP error {response.status_code}")
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram API error in {method}")
        return payload.get("result")

    async def get_updates(
        self, offset: int | None = None, timeout: int = 25
    ) -> list[dict[str, Any]]:
        result = await self._call(
            "getUpdates",
            offset=offset,
            timeout=timeout,
            allowed_updates=["message", "callback_query"],
        )
        return list(result or [])

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = await self._call(
            "sendMessage", chat_id=chat_id, text=text, reply_markup=reply_markup
        )
        return dict(result)

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = await self._call(
            "editMessageText",
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
        )
        return dict(result or {})

    async def answer_callback_query(self, callback_query_id: str, text: str) -> None:
        await self._call("answerCallbackQuery", callback_query_id=callback_query_id, text=text)

    async def close(self) -> None:
        await self._http.aclose()
