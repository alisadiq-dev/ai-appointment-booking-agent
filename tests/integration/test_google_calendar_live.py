"""Live checks against the REAL Google Calendar API, using a dedicated test calendar.

Opt-in: skipped unless GOOGLE_CALENDAR_ID and GOOGLE_SERVICE_ACCOUNT_JSON are set (fails
instead when REQUIRE_DB=1). The calendar must be shared with the service account
("Make changes to events"); see docs/google-calendar.md.

Safety: everything created here uses a far-future window (March-April 2031) and a summary
starting with "LIVE TEST". Each test cleans up after itself, and a module fixture sweeps that
window before and after, then asserts that nothing was left behind. Events outside the window
or without the prefix are never touched.
"""

import logging
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from googleapiclient.errors import HttpError

from app.core.config import Settings
from app.integrations.google_calendar import APP_SOURCE, GoogleCalendar
from app.schemas.calendar import CalendarEvent
from app.schemas.time_range import TimeRange
from app.services.errors import CalendarEventNotFoundError, CalendarUnavailableError
from tests.db_requirements import check_env

pytestmark = [pytest.mark.live_google, pytest.mark.requires_google]

PREFIX = "LIVE TEST"
WINDOW_START = datetime(2031, 3, 1, tzinfo=UTC)
WINDOW_END = datetime(2031, 4, 30, tzinfo=UTC)


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2031, 3, day, hour, minute, tzinfo=UTC)


@dataclass
class Live:
    calendar: GoogleCalendar
    run_id: str

    @property
    def _api(self) -> Any:
        return self.calendar._events()

    @property
    def _calendar_id(self) -> str:
        return self.calendar._calendar_id

    def summary(self, label: str) -> str:
        return f"{PREFIX} {self.run_id} {label}"

    def get(self, event_id: str) -> dict[str, Any]:
        return self._api.get(calendarId=self._calendar_id, eventId=event_id).execute()

    def get_or_none(self, event_id: str) -> dict[str, Any] | None:
        """The event, or None if Google says it is gone (404/410)."""
        try:
            return self.get(event_id)
        except HttpError as exc:
            if exc.resp.status in (404, 410):
                return None
            raise

    def insert_manual(self, body: dict[str, Any]) -> str:
        """An event as a human would add it: no app marker."""
        body = {**body, "summary": self.summary(body.pop("label"))}
        return str(self._api.insert(calendarId=self._calendar_id, body=body).execute()["id"])

    def delete(self, event_id: str) -> None:
        self.calendar.delete_event(event_id)

    def sweep(self) -> int:
        """Delete every LIVE TEST event in the dedicated window; returns how many."""
        deleted, page_token = 0, None
        while True:
            page = self._api.list(
                calendarId=self._calendar_id,
                timeMin=WINDOW_START.isoformat(),
                timeMax=WINDOW_END.isoformat(),
                singleEvents=True,
                showDeleted=False,
                maxResults=250,
                pageToken=page_token,
            ).execute()
            for item in page.get("items", []):
                if item.get("summary", "").startswith(PREFIX):
                    self.calendar.delete_event(item["id"])
                    deleted += 1
            page_token = page.get("nextPageToken")
            if not page_token:
                return deleted


@pytest.fixture(scope="module")
def live() -> Iterator[Live]:
    # Module-scoped fixtures run before the per-test requirements check, so check here first.
    check_env("requires_google", os.environ)
    ctx = Live(GoogleCalendar.from_settings(Settings(_env_file=None)), uuid.uuid4().hex[:8])
    stale = ctx.sweep()  # leftovers from an earlier interrupted run
    yield ctx
    swept = ctx.sweep()
    leftover = ctx.sweep()
    print(f"\nLIVE-CLEANUP: stale_before={stale} swept_at_end={swept} leftover_after={leftover}")
    assert leftover == 0, "live test left events behind"


def _instant(value: dict[str, str]) -> datetime:
    return datetime.fromisoformat(value["dateTime"]).astimezone(UTC)


def test_event_lifecycle_create_update_delete(live: Live) -> None:
    booking_id = uuid.uuid4()
    event = CalendarEvent(
        booking_id=booking_id,
        summary=live.summary("lifecycle"),
        description=f"Booking {booking_id}",
        start=at(4, 5),
        end=at(4, 5, 30),
    )
    event_id = ""
    try:
        event_id = live.calendar.create_event(event)

        assert event_id == booking_id.hex
        created = live.get(event_id)
        assert created["summary"] == event.summary
        assert (_instant(created["start"]), _instant(created["end"])) == (at(4, 5), at(4, 5, 30))
        assert created["start"]["timeZone"] == "Asia/Karachi"
        assert created["extendedProperties"]["private"]["source"] == APP_SOURCE
        assert created["extendedProperties"]["private"]["booking_id"] == str(booking_id)
        assert "attendees" not in created

        live.calendar.update_event(event_id, at(4, 7), at(4, 7, 30))

        moved = live.get(event_id)
        assert moved["id"] == event_id  # same event, moved in place
        assert (_instant(moved["start"]), _instant(moved["end"])) == (at(4, 7), at(4, 7, 30))

        live.calendar.delete_event(event_id)

        gone = live.get_or_none(event_id)  # deleted events may still be readable by id
        assert gone is None or gone["status"] == "cancelled"

        live.calendar.delete_event(event_id)  # deleting twice is fine
        with pytest.raises(CalendarEventNotFoundError):  # Google answers 200 + "cancelled"
            live.calendar.update_event(event_id, at(4, 8), at(4, 8, 30))
        with pytest.raises(CalendarUnavailableError):  # a deleted event's id can never be reused
            live.calendar.create_event(event)
    finally:
        if event_id:
            live.calendar.delete_event(event_id)


def test_creating_the_same_booking_twice_makes_one_event(live: Live) -> None:
    booking_id = uuid.uuid4()
    event = CalendarEvent(booking_id, live.summary("idempotent"), "x", at(5, 5), at(5, 5, 30))
    try:
        first = live.calendar.create_event(event)
        second = live.calendar.create_event(event)  # e.g. a retry after a lost response

        assert first == second == booking_id.hex
        items = live._api.list(
            calendarId=live._calendar_id,
            timeMin=at(5, 0).isoformat(),
            timeMax=at(6, 0).isoformat(),
            singleEvents=True,
        ).execute()["items"]
        assert [
            i["id"] for i in items if i.get("summary", "").startswith(live.summary("idem"))
        ] == [booking_id.hex]
    finally:
        live.calendar.delete_event(booking_id.hex)


def test_list_busy_sees_manual_events_but_not_free_or_app_created_ones(live: Live) -> None:
    created: list[str] = []
    try:
        timed = live.insert_manual(
            {
                "label": "manual-busy",
                "start": {"dateTime": at(10, 5).isoformat()},
                "end": {"dateTime": at(10, 6).isoformat()},
            }
        )
        free = live.insert_manual(
            {
                "label": "manual-free",
                "transparency": "transparent",
                "start": {"dateTime": at(10, 7).isoformat()},
                "end": {"dateTime": at(10, 8).isoformat()},
            }
        )
        all_day = live.insert_manual(
            {
                "label": "manual-all-day",
                "start": {"date": "2031-03-11"},
                "end": {"date": "2031-03-12"},
            }
        )
        created += [timed, free, all_day]
        app_id = live.calendar.create_event(
            CalendarEvent(uuid.uuid4(), live.summary("app-made"), "x", at(10, 9), at(10, 9, 30))
        )
        created.append(app_id)

        busy = live.calendar.list_busy(at(10, 0), at(13, 0))

        assert TimeRange(at(10, 5), at(10, 6)) in busy  # manual timed event
        # all-day on 2031-03-11 in Karachi (UTC+5) is 19:00Z the day before to 19:00Z that day
        assert TimeRange(at(10, 19), at(11, 19)) in busy
        assert not any(
            r.overlaps(at(10, 7), at(10, 8)) for r in busy if r.end - r.start < timedelta(hours=2)
        )
        assert not any(
            r.overlaps(at(10, 9), at(10, 9, 30))
            for r in busy
            if r.end - r.start < timedelta(hours=2)
        )
    finally:
        for event_id in created:
            live.delete(event_id)

    assert live.calendar.list_busy(at(10, 0), at(13, 0)) == []  # nothing left blocking


def test_a_calendar_that_does_not_exist_fails_closed_with_an_actionable_log(
    live: Live, caplog: pytest.LogCaptureFixture
) -> None:
    bogus = GoogleCalendar(
        f"does-not-exist-{uuid.uuid4().hex}@group.calendar.google.com",
        live.calendar._zone,
        live.calendar._http_factory,
        num_retries=0,
    )
    event = CalendarEvent(uuid.uuid4(), live.summary("bogus"), "x", at(20, 5), at(20, 5, 30))

    with caplog.at_level(logging.ERROR), pytest.raises(CalendarUnavailableError):
        bogus.create_event(event)

    text = " ".join(r.getMessage() for r in caplog.records)
    assert "create_event" in text
    assert "shared" in text
