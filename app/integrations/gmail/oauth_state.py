"""Signed, short-lived OAuth state without storing secrets in the URL."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time


class OAuthStateError(ValueError):
    """The OAuth state is malformed, expired or signed by another app."""


def create_state(user_id: str, signing_key: str, now: int | None = None) -> str:
    issued_at = int(now or time.time())
    payload = f"{user_id}.{issued_at}"
    signature = _sign(payload, signing_key)
    encoded = base64.urlsafe_b64encode(f"{payload}.{signature}".encode()).decode()
    return encoded.rstrip("=")


def verify_state(
    state: str,
    signing_key: str,
    now: int | None = None,
    max_age_seconds: int = 600,
) -> str:
    try:
        decoded = base64.urlsafe_b64decode(state + "=" * (-len(state) % 4)).decode()
        user_id, issued_text, signature = decoded.split(".", 2)
        issued_at = int(issued_text)
    except (ValueError, UnicodeDecodeError) as exc:
        raise OAuthStateError("invalid OAuth state") from exc
    payload = f"{user_id}.{issued_at}"
    if not hmac.compare_digest(signature, _sign(payload, signing_key)):
        raise OAuthStateError("invalid OAuth state signature")
    current = int(now or time.time())
    if issued_at > current or current - issued_at > max_age_seconds:
        raise OAuthStateError("expired OAuth state")
    return user_id


def _sign(payload: str, signing_key: str) -> str:
    return hmac.new(
        signing_key.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
