"""Provider abstraction and OpenAI-compatible transport."""

from collections.abc import Mapping
from typing import Protocol

import httpx


class LLMProviderError(RuntimeError):
    """A provider failure safe to expose in application logs."""


class LLMProvider(Protocol):
    async def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
    ) -> str: ...


class OpenAICompatibleProvider:
    """Call an OpenAI-compatible chat-completions endpoint.

    OpenClaw exposes this surface when its chat-completions endpoint is
    enabled. The provider deliberately returns text only; schema validation and
    repair policy live in ``LLMService``.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout_seconds: int = 60,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._endpoint = self._chat_completions_url(base_url)
        self._api_key = api_key
        self._http = http_client or httpx.AsyncClient(timeout=timeout_seconds)

    async def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
    ) -> str:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        try:
            response = await self._http.post(self._endpoint, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise LLMProviderError("LLM provider request failed") from exc
        if response.is_error:
            raise LLMProviderError(f"LLM provider HTTP error {response.status_code}")
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMProviderError("LLM provider returned an invalid response") from exc
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts = [
                item.get("text", "")
                for item in content
                if isinstance(item, Mapping) and isinstance(item.get("text"), str)
            ]
            return "".join(text_parts)
        raise LLMProviderError("LLM provider returned non-text content")

    @staticmethod
    def _chat_completions_url(base_url: str) -> str:
        normalized = base_url.rstrip("/")
        if normalized.endswith("/chat/completions"):
            return normalized
        if normalized.endswith("/v1"):
            return f"{normalized}/chat/completions"
        return f"{normalized}/v1/chat/completions"

    async def close(self) -> None:
        await self._http.aclose()
