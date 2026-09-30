from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, read from environment variables (and a local .env in dev)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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
