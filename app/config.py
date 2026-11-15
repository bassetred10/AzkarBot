from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    telegram_bot_token: str = Field(min_length=20)
    admin_ids_raw: str = Field(default="", alias="ADMIN_IDS")
    required_channel: str = ""
    webhook_base_url: str = ""
    webhook_secret: str = ""
    database_path: str = "data/athar.sqlite3"
    log_level: str = "INFO"
    port: int = 8000

    @field_validator("telegram_bot_token")
    @classmethod
    def strip_token(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")
        return value

    @field_validator("webhook_base_url")
    @classmethod
    def normalize_webhook_url(cls, value: str) -> str:
        return value.strip().rstrip("/")

    @field_validator("required_channel")
    @classmethod
    def normalize_channel(cls, value: str) -> str:
        return value.strip()

    @property
    def admin_ids(self) -> set[int]:
        ids: set[int] = set()
        for raw in self.admin_ids_raw.split(","):
            raw = raw.strip()
            if raw:
                try:
                    ids.add(int(raw))
                except ValueError as exc:
                    raise ValueError("ADMIN_IDS must contain comma-separated numeric Telegram IDs") from exc
        return ids

    @model_validator(mode="after")
    def validate_webhook(self):
        if self.webhook_base_url and len(self.webhook_secret) < 32:
            raise ValueError("WEBHOOK_SECRET must be at least 32 characters when webhook mode is enabled")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
