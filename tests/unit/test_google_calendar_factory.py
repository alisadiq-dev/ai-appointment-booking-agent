import threading
from zoneinfo import ZoneInfo

import pytest
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.http import HttpMockSequence

from app.core.config import Settings
from app.integrations.google_calendar import SCOPES, GoogleCalendar
from tests.google_helpers import fake_service_account_json


def test_from_settings_builds_a_service_account_client_with_least_privilege_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CALENDAR_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_CALENDAR_ID", "cal@group.calendar.google.com")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", fake_service_account_json())
    monkeypatch.setenv("BUSINESS_TIMEZONE", "Asia/Karachi")
    monkeypatch.setenv("CALENDAR_TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("CALENDAR_NUM_RETRIES", "2")

    calendar = GoogleCalendar.from_settings(Settings(_env_file=None))

    assert SCOPES == ["https://www.googleapis.com/auth/calendar.events"]
    http = calendar._http_factory()
    assert isinstance(http, AuthorizedHttp)
    assert http.credentials.scopes == SCOPES
    assert http.credentials.service_account_email == "svc@example.iam.gserviceaccount.com"
    assert http.http.timeout == 7
    assert calendar._num_retries == 2
    assert calendar._zone == ZoneInfo("Asia/Karachi")


def test_http_objects_are_created_once_per_thread_and_reused() -> None:
    # httplib2 is not thread-safe and FastAPI runs sync endpoints in a threadpool.
    created: list[HttpMockSequence] = []

    def factory() -> HttpMockSequence:
        created.append(HttpMockSequence([({"status": "204"}, "")] * 5))
        return created[-1]

    calendar = GoogleCalendar("cal", ZoneInfo("UTC"), http_factory=factory)  # type: ignore[arg-type]
    assert created == []  # lazy

    calendar.delete_event("abcde")
    calendar.delete_event("abcde")
    assert len(created) == 1  # reused within a thread

    worker = threading.Thread(target=calendar.delete_event, args=("abcde",))
    worker.start()
    worker.join()
    assert len(created) == 2  # a different thread got its own
    assert len(created[0].request_sequence) == 2
    assert len(created[1].request_sequence) == 1


def test_from_settings_requires_the_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOOGLE_CALENDAR_ID", raising=False)
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)

    with pytest.raises(ValueError, match="required"):
        GoogleCalendar.from_settings(Settings(_env_file=None, calendar_enabled=False))
