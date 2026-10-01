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


# ------------------------------------------------------------------ Google Calendar settings

_SA_JSON = '{"client_email": "svc@example.iam.gserviceaccount.com", "private_key": "TOPSECRETKEY"}'


def test_calendar_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("CALENDAR_ENABLED", "GOOGLE_CALENDAR_ID", "GOOGLE_SERVICE_ACCOUNT_JSON"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.calendar_enabled is False
    assert settings.google_calendar_id is None
    assert settings.google_service_account_json is None
    assert settings.calendar_timeout_seconds == 10
    assert settings.calendar_num_retries == 3


def test_enabled_calendar_needs_both_the_id_and_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALENDAR_ENABLED", "true")
    monkeypatch.delenv("GOOGLE_CALENDAR_ID", raising=False)
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", _SA_JSON)

    with pytest.raises(ValidationError, match="GOOGLE_CALENDAR_ID"):
        Settings(_env_file=None)


def test_enabled_calendar_rejects_a_key_that_is_not_service_account_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CALENDAR_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_CALENDAR_ID", "cal@group.calendar.google.com")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "not json TOPSECRETKEY")

    with pytest.raises(ValidationError, match="GOOGLE_SERVICE_ACCOUNT_JSON") as info:
        Settings(_env_file=None)
    assert "TOPSECRETKEY" not in str(info.value)  # the value never appears in the error


def test_enabled_calendar_requires_client_email_and_private_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CALENDAR_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_CALENDAR_ID", "cal@group.calendar.google.com")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", '{"type": "service_account"}')

    with pytest.raises(ValidationError, match="client_email"):
        Settings(_env_file=None)


def test_valid_calendar_settings_load_and_never_appear_in_repr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CALENDAR_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_CALENDAR_ID", "cal-id-123@group.calendar.google.com")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", _SA_JSON)

    settings = Settings(_env_file=None)

    assert settings.calendar_enabled is True
    assert settings.google_calendar_id is not None
    assert settings.google_calendar_id.get_secret_value() == "cal-id-123@group.calendar.google.com"
    text = repr(settings) + str(settings) + settings.model_dump_json()
    assert "TOPSECRETKEY" not in text
    assert "cal-id-123" not in text


def test_disabled_calendar_does_not_validate_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALENDAR_ENABLED", "false")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "garbage")

    assert Settings(_env_file=None).calendar_enabled is False


def test_enabled_calendar_needs_the_key_to_be_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALENDAR_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_CALENDAR_ID", "cal@group.calendar.google.com")
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)

    with pytest.raises(ValidationError, match="GOOGLE_SERVICE_ACCOUNT_JSON is not set"):
        Settings(_env_file=None)
