import json
from functools import lru_cache
from typing import Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, read from environment variables (and a local .env in dev)."""

    # hide_input_in_errors: a failed validation must never echo setting values (they include
    # credentials) into tracebacks or logs.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    app_env: str = "development"
    log_level: str = "INFO"

    supabase_url: str | None = None
    supabase_jwt_audience: str = "authenticated"
    jwks_cache_seconds: float = 300
    jwks_timeout_seconds: float = 5

    database_url: str | None = None
    db_pool_min_size: int = Field(default=1, ge=1)
    db_pool_max_size: int = Field(default=10, ge=1)
    db_pool_timeout_seconds: float = Field(default=10, gt=0)

    business_timezone: str = "Asia/Karachi"
    slot_interval_minutes: int = Field(default=15, gt=0, le=1440)

    # Google Calendar (service account; the calendar is shared with the service account's email).
    # Secrets are SecretStr so they never appear in reprs, logs or validation errors.
    calendar_enabled: bool = False
    google_calendar_id: SecretStr | None = None
    google_service_account_json: SecretStr | None = None
    calendar_timeout_seconds: float = Field(default=10, gt=0)
    calendar_num_retries: int = Field(default=3, ge=0, le=8)

    # Gemini (language model for the booking agent). Without a key the agent answers with a safe
    # fallback message and never books.
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_timeout_seconds: float = Field(default=10, gt=0)

    # Chat rate limit: per user id, in memory, PER INSTANCE (see app/core/rate_limit.py).
    chat_rate_limit_requests: int = Field(default=10, gt=0)
    chat_rate_limit_window_seconds: float = Field(default=60, gt=0)

    @model_validator(mode="after")
    def _calendar_config_is_complete(self) -> Self:
        if not self.calendar_enabled:
            return self
        calendar_id = self.google_calendar_id
        if calendar_id is None or not calendar_id.get_secret_value().strip():
            raise ValueError("CALENDAR_ENABLED is true but GOOGLE_CALENDAR_ID is not set")
        key = self.google_service_account_json
        if key is None or not key.get_secret_value().strip():
            raise ValueError("CALENDAR_ENABLED is true but GOOGLE_SERVICE_ACCOUNT_JSON is not set")
        try:
            data = json.loads(key.get_secret_value())
        except ValueError:
            raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON") from None
        if (
            not isinstance(data, dict)
            or not data.get("client_email")
            or not data.get("private_key")
        ):
            raise ValueError(
                "GOOGLE_SERVICE_ACCOUNT_JSON must contain client_email and private_key"
            )
        return self

    @field_validator("business_timezone")
    @classmethod
    def _timezone_must_exist(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone: {value}") from exc
        return value

    @property
    def business_zone(self) -> ZoneInfo:
        return ZoneInfo(self.business_timezone)

    @property
    def jwt_issuer(self) -> str | None:
        if self.supabase_url is None:
            return None
        return f"{self.supabase_url.rstrip('/')}/auth/v1"

    @property
    def jwks_url(self) -> str | None:
        if self.jwt_issuer is None:
            return None
        return f"{self.jwt_issuer}/.well-known/jwks.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
