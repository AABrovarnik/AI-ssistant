"""Encryption for OAuth tokens at rest."""

from cryptography.fernet import Fernet, InvalidToken


class TokenEncryptionError(RuntimeError):
    """The configured token key is absent or cannot decrypt a token."""


class TokenCipher:
    def __init__(self, key: str | None) -> None:
        if not key:
            raise TokenEncryptionError("TOKEN_ENCRYPTION_KEY is required for Gmail OAuth")
        try:
            self._fernet = Fernet(key.encode())
        except (TypeError, ValueError) as exc:
            raise TokenEncryptionError("TOKEN_ENCRYPTION_KEY is not a valid Fernet key") from exc

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode()).decode()
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise TokenEncryptionError("Gmail OAuth token cannot be decrypted") from exc
