import pytest

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
