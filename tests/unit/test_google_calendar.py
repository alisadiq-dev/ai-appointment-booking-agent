"""GoogleCalendar adapter against a mocked HTTP transport (no network, no credentials)."""

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import UUID
from zoneinfo import ZoneInfo

import httplib2
import pytest
from googleapiclient.http import HttpMockSequence

from app.integrations.google_calendar import APP_SOURCE, GoogleCalendar
from app.schemas.calendar import CalendarEvent
from app.schemas.time_range import TimeRange
from app.services.errors import CalendarEventNotFoundError, CalendarUnavailableError

KARACHI = ZoneInfo("Asia/Karachi")
CAL = "business@group.calendar.google.com"
BOOKING = UUID("11111111-2222-3333-4444-555555555555")
START = datetime(2031, 3, 4, 5, 0, tzinfo=UTC)
END = START + timedelta(minutes=30)


def ok(body: dict[str, Any] | None = None, status: str = "200") -> tuple[dict[str, str], str]:
    return {"status": status, "content-type": "application/json"}, json.dumps(body or {})


def error(status: int, reason: str = "backendError") -> tuple[dict[str, str], str]:
    body = {"error": {"code": status, "message": reason, "errors": [{"reason": reason}]}}
    return {"status": str(status), "content-type": "application/json"}, json.dumps(body)


def make(*responses: Any, retries: int = 3) -> tuple[GoogleCalendar, HttpMockSequence]:
    http = HttpMockSequence(list(responses))
    return GoogleCalendar(CAL, KARACHI, http_factory=lambda: http, num_retries=retries), http


def requests_of(http: HttpMockSequence) -> list[tuple[str, str, Any]]:
    return [(uri, method, body) for uri, method, body, _ in http.request_sequence]


@pytest.fixture(autouse=True)
def no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)


EVENT = CalendarEvent(
    booking_id=BOOKING,
    summary="Haircut - Alice",
    description=f"Booking {BOOKING}",
    start=START,
    end=END,
)


# ---------------------------------------------------------------------------- create


def test_create_event_posts_the_expected_body_and_returns_the_event_id() -> None:
    calendar, http = make(ok({"id": BOOKING.hex}))

    event_id = calendar.create_event(EVENT)

    assert event_id == BOOKING.hex
    [(uri, method, body)] = requests_of(http)
    assert method == "POST"
    assert urlparse(uri).path.endswith(f"/calendars/{CAL.replace('@', '%40')}/events")
    sent = json.loads(body)
    assert sent["id"] == BOOKING.hex  # deterministic: retries cannot create duplicates
    assert sent["summary"] == "Haircut - Alice"
    assert sent["description"] == f"Booking {BOOKING}"
    assert sent["start"] == {"dateTime": "2031-03-04T05:00:00+00:00", "timeZone": "Asia/Karachi"}
    assert sent["end"] == {"dateTime": "2031-03-04T05:30:00+00:00", "timeZone": "Asia/Karachi"}
    assert sent["extendedProperties"]["private"] == {
        "booking_id": str(BOOKING),
        "source": APP_SOURCE,
    }
    assert "attendees" not in sent  # service accounts cannot invite attendees


def test_the_event_id_is_valid_for_google() -> None:
    # Google allows only lowercase a-v and digits 0-9, 5 to 1024 characters.
    assert all(c in "0123456789abcdefghijklmnopqrstuv" for c in BOOKING.hex)
    assert 5 <= len(BOOKING.hex) <= 1024


def test_create_treats_a_409_as_already_created() -> None:
    calendar, http = make(error(409, "duplicate"))

    assert calendar.create_event(EVENT) == BOOKING.hex
    assert len(requests_of(http)) == 1  # a duplicate is not retried


def test_create_retries_a_server_error_then_succeeds() -> None:
    calendar, http = make(error(503), error(500), ok({"id": BOOKING.hex}))

    assert calendar.create_event(EVENT) == BOOKING.hex
    assert len(requests_of(http)) == 3


def test_create_retries_rate_limit_errors() -> None:
    calendar, http = make(
        error(403, "rateLimitExceeded"), error(429, "rateLimitExceeded"), ok({"id": "x"})
    )

    calendar.create_event(EVENT)

    assert len(requests_of(http)) == 3


def test_create_gives_up_after_the_retries_and_reports_unavailable() -> None:
    calendar, http = make(*[error(503)] * 10, retries=2)

    with pytest.raises(CalendarUnavailableError):
        calendar.create_event(EVENT)
    assert len(requests_of(http)) == 3  # first try plus two retries


@pytest.mark.parametrize("status", [401, 403, 404])
def test_create_does_not_retry_permission_or_missing_calendar_errors(status: int) -> None:
    calendar, http = make(error(status, "forbidden"))

    with pytest.raises(CalendarUnavailableError):
        calendar.create_event(EVENT)
    assert len(requests_of(http)) == 1


def test_a_calendar_that_is_not_shared_logs_an_actionable_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calendar, _ = make(error(404, "notFound"))

    with caplog.at_level(logging.ERROR), pytest.raises(CalendarUnavailableError):
        calendar.create_event(EVENT)

    text = " ".join(r.getMessage() for r in caplog.records)
    assert "create_event" in text
    assert "404" in text
    assert "shared" in text
    assert CAL not in text  # identifiers from configuration are not logged


# ---------------------------------------------------------------------------- update


def test_update_event_patches_only_the_times() -> None:
    calendar, http = make(ok({"id": BOOKING.hex}))
    new_start = START + timedelta(hours=1)

    calendar.update_event(BOOKING.hex, new_start, new_start + timedelta(minutes=30))

    [(uri, method, body)] = requests_of(http)
    assert method == "PATCH"
    assert urlparse(uri).path.endswith(f"/events/{BOOKING.hex}")
    assert json.loads(body) == {
        "start": {"dateTime": "2031-03-04T06:00:00+00:00", "timeZone": "Asia/Karachi"},
        "end": {"dateTime": "2031-03-04T06:30:00+00:00", "timeZone": "Asia/Karachi"},
    }


@pytest.mark.parametrize("status", [404, 410])
def test_update_of_a_missing_event_raises_event_not_found(status: int) -> None:
    calendar, _ = make(error(status, "notFound"))

    with pytest.raises(CalendarEventNotFoundError):
        calendar.update_event(BOOKING.hex, START, END)


def test_update_reports_other_failures_as_unavailable() -> None:
    calendar, _ = make(*[error(500)] * 5)

    with pytest.raises(CalendarUnavailableError):
        calendar.update_event(BOOKING.hex, START, END)


# ---------------------------------------------------------------------------- delete


def test_delete_event_sends_a_delete() -> None:
    calendar, http = make(({"status": "204"}, ""))

    calendar.delete_event(BOOKING.hex)

    [(uri, method, _)] = requests_of(http)
    assert method == "DELETE"
    assert urlparse(uri).path.endswith(f"/events/{BOOKING.hex}")


@pytest.mark.parametrize("status", [404, 410])
def test_deleting_an_already_deleted_event_is_success(status: int) -> None:
    calendar, _ = make(error(status, "notFound"))

    calendar.delete_event(BOOKING.hex)  # no exception


def test_delete_reports_other_failures_as_unavailable() -> None:
    calendar, _ = make(*[error(500)] * 5)

    with pytest.raises(CalendarUnavailableError):
        calendar.delete_event(BOOKING.hex)


# -------------------------------------------------------------------------- transport


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("timed out"),
        httplib2.ServerNotFoundError("dns"),
        ConnectionResetError("reset"),
    ],
)
def test_transport_failures_fail_closed_and_log_only_the_error_type(
    failure: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    class Broken:
        def request(self, *_: Any, **__: Any) -> None:
            raise failure

    calendar = GoogleCalendar(CAL, KARACHI, http_factory=lambda: Broken(), num_retries=0)  # type: ignore[arg-type,return-value]

    with caplog.at_level(logging.ERROR), pytest.raises(CalendarUnavailableError):
        calendar.create_event(EVENT)

    text = " ".join(r.getMessage() for r in caplog.records)
    assert type(failure).__name__ in text


# ---------------------------------------------------------------------------- list_busy


def _item(
    start: str,
    end: str,
    *,
    status: str = "confirmed",
    transparency: str | None = None,
    source: str | None = None,
    all_day: bool = False,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": "e" + start,
        "status": status,
        "start": {"date": start} if all_day else {"dateTime": start},
        "end": {"date": end} if all_day else {"dateTime": end},
    }
    if transparency:
        item["transparency"] = transparency
    if source:
        item["extendedProperties"] = {"private": {"source": source}}
    return item


def test_list_busy_asks_for_single_events_in_the_window() -> None:
    calendar, http = make(ok({"items": []}))
    window_start, window_end = START, START + timedelta(days=1)

    assert calendar.list_busy(window_start, window_end) == []

    [(uri, method, _)] = requests_of(http)
    query = parse_qs(urlparse(uri).query)
    assert method == "GET"
    assert query["singleEvents"] == ["true"]
    assert query["showDeleted"] == ["false"]
    assert query["timeMin"] == ["2031-03-04T05:00:00+00:00"]
    assert query["timeMax"] == ["2031-03-05T05:00:00+00:00"]


def test_list_busy_returns_manual_events_as_utc_ranges() -> None:
    calendar, _ = make(
        ok({"items": [_item("2031-03-04T10:30:00+05:00", "2031-03-04T11:30:00+05:00")]})
    )

    busy = calendar.list_busy(START, START + timedelta(days=1))

    assert busy == [
        TimeRange(datetime(2031, 3, 4, 5, 30, tzinfo=UTC), datetime(2031, 3, 4, 6, 30, tzinfo=UTC))
    ]
    assert all(r.start.utcoffset() == timedelta(0) for r in busy)


def test_list_busy_ignores_cancelled_free_and_app_created_events() -> None:
    items = [
        _item("2031-03-04T06:00:00+00:00", "2031-03-04T07:00:00+00:00", status="cancelled"),
        _item("2031-03-04T08:00:00+00:00", "2031-03-04T09:00:00+00:00", transparency="transparent"),
        _item("2031-03-04T10:00:00+00:00", "2031-03-04T11:00:00+00:00", source=APP_SOURCE),
        _item("2031-03-04T12:00:00+00:00", "2031-03-04T13:00:00+00:00", source="someone-else"),
    ]
    calendar, _ = make(ok({"items": items}))

    busy = calendar.list_busy(START, START + timedelta(days=1))

    assert busy == [
        TimeRange(datetime(2031, 3, 4, 12, 0, tzinfo=UTC), datetime(2031, 3, 4, 13, 0, tzinfo=UTC))
    ]


def test_list_busy_treats_an_all_day_event_as_the_whole_local_day() -> None:
    calendar, _ = make(ok({"items": [_item("2031-03-05", "2031-03-06", all_day=True)]}))

    busy = calendar.list_busy(START, START + timedelta(days=3))

    # Karachi is UTC+5: local midnight to midnight is 19:00Z the day before.
    assert busy == [
        TimeRange(datetime(2031, 3, 4, 19, 0, tzinfo=UTC), datetime(2031, 3, 5, 19, 0, tzinfo=UTC))
    ]


def test_list_busy_follows_pagination_and_sorts() -> None:
    page_1 = {
        "items": [_item("2031-03-04T12:00:00+00:00", "2031-03-04T13:00:00+00:00")],
        "nextPageToken": "t2",
    }
    page_2 = {"items": [_item("2031-03-04T06:00:00+00:00", "2031-03-04T07:00:00+00:00")]}
    calendar, http = make(ok(page_1), ok(page_2))

    busy = calendar.list_busy(START, START + timedelta(days=1))

    assert [r.start.hour for r in busy] == [6, 12]
    assert parse_qs(urlparse(requests_of(http)[1][0]).query)["pageToken"] == ["t2"]


def test_list_busy_fails_closed_instead_of_returning_partial_results() -> None:
    page = {"items": [], "nextPageToken": "again"}
    calendar, _ = make(*[ok(page)] * 50)

    with pytest.raises(CalendarUnavailableError):
        calendar.list_busy(START, START + timedelta(days=1))


def test_list_busy_reports_failures_as_unavailable() -> None:
    calendar, _ = make(*[error(500)] * 5)

    with pytest.raises(CalendarUnavailableError):
        calendar.list_busy(START, START + timedelta(days=1))
