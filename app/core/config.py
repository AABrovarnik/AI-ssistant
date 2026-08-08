from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "development"
    app_name: str = "ai-secretary"
    log_level: str = "INFO"
    timezone: str = "UTC"

    api_bind_host: str = "127.0.0.1"
    api_port: int = 8000
    internal_api_token: str = Field(default="dev-only-change-me", repr=False)

    database_url: str = "sqlite+aiosqlite:///./ai-secretary-dev.sqlite"

    telegram_bot_token: str | None = Field(default=None, repr=False)
    telegram_owner_user_id: int | None = None
    telegram_mode: str = "polling"

    llm_provider: str = "openclaw"
    openclaw_base_url: str = "http://127.0.0.1:18789"
    openclaw_api_key: str | None = Field(default=None, repr=False)
    llm_model: str = "openai/gpt-5.6-luna"
    llm_temperature: float = 0.1
    llm_timeout_seconds: int = 60

    gmail_enabled: bool = False
    google_calendar_enabled: bool = False
    token_encryption_key: str | None = Field(default=None, repr=False)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
