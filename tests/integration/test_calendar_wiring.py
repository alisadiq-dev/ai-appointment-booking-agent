"""How the app picks and starts its calendar client."""

import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_calendar
from app.core.config import get_settings
from app.integrations.google_calendar import GoogleCalendar
from app.integrations.null_calendar import NullCalendar
from app.main import create_app
from tests.google_helpers import fake_service_account_json


@pytest.fixture(autouse=True)
def fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "DATABASE_URL",
        "CALENDAR_ENABLED",
        "GOOGLE_CALENDAR_ID",
        "GOOGLE_SERVICE_ACCOUNT_JSON",
    ):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    get_calendar.cache_clear()
    yield  # type: ignore[misc]
    get_settings.cache_clear()
    get_calendar.cache_clear()


def _enable(monkeypatch: pytest.MonkeyPatch, key_json: str) -> None:
    monkeypatch.setenv("CALENDAR_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_CALENDAR_ID", "cal@group.calendar.google.com")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", key_json)


def test_calendar_is_a_no_op_when_disabled() -> None:
    assert isinstance(get_calendar(), NullCalendar)


def test_a_disabled_calendar_logs_a_clear_warning_at_startup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING), TestClient(create_app()):
        pass

    assert any("CALENDAR_ENABLED is false" in r.getMessage() for r in caplog.records)


def test_an_enabled_calendar_is_google_and_one_shared_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable(monkeypatch, fake_service_account_json())

    first, second = get_calendar(), get_calendar()

    assert isinstance(first, GoogleCalendar)
    assert first is second  # one client: it holds the credentials and refreshes tokens


def test_an_enabled_calendar_builds_its_client_at_startup(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _enable(monkeypatch, fake_service_account_json())

    with caplog.at_level(logging.WARNING), TestClient(create_app()):
        assert get_calendar.cache_info().currsize == 1  # built by the lifespan, not first use

    assert not any("CALENDAR_ENABLED is false" in r.getMessage() for r in caplog.records)


def test_a_key_that_looks_fine_but_cannot_be_loaded_stops_startup_without_leaking_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken = json.dumps(
        {"client_email": "svc@example.iam.gserviceaccount.com", "private_key": "SENTINELKEY123"}
    )
    _enable(monkeypatch, broken)

    with pytest.raises(Exception, match=r"\S") as info, TestClient(create_app()):
        pass

    assert "SENTINELKEY123" not in str(info.value)
