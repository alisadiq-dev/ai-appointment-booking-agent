from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    settings = Settings()

    assert settings.app_env == "production"
    assert settings.log_level == "DEBUG"


def test_settings_have_safe_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    settings = Settings(_env_file=None)

    assert settings.app_env == "development"
    assert settings.log_level == "INFO"


def test_auth_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "SUPABASE_URL",
        "SUPABASE_JWT_AUDIENCE",
        "JWKS_CACHE_SECONDS",
        "JWKS_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.supabase_url is None
    assert settings.supabase_jwt_audience == "authenticated"
    assert settings.jwks_cache_seconds == 300
    assert settings.jwks_timeout_seconds == 5


def test_issuer_and_jwks_url_are_derived_from_supabase_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1:54321/")

    settings = Settings(_env_file=None)

    assert settings.jwt_issuer == "http://127.0.0.1:54321/auth/v1"
    assert settings.jwks_url == "http://127.0.0.1:54321/auth/v1/.well-known/jwks.json"


def test_issuer_is_none_when_supabase_url_is_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)

    settings = Settings(_env_file=None)

    assert settings.jwt_issuer is None
    assert settings.jwks_url is None


def test_booking_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("BUSINESS_TIMEZONE", "SLOT_INTERVAL_MINUTES", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.business_timezone == "Asia/Karachi"
    assert settings.business_zone == ZoneInfo("Asia/Karachi")
    assert settings.slot_interval_minutes == 15
    assert settings.database_url is None


def test_business_timezone_can_be_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUSINESS_TIMEZONE", "Europe/London")

    assert Settings(_env_file=None).business_zone == ZoneInfo("Europe/London")


def test_invalid_business_timezone_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUSINESS_TIMEZONE", "Not/AZone")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("value", ["0", "-5", "1441"])
def test_invalid_slot_interval_is_rejected(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("SLOT_INTERVAL_MINUTES", value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
