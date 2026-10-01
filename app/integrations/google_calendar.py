"""Google Calendar behind the CalendarPort interface, authenticated as a service account.

The business calendar is shared with the service account's email ("Make changes to events") and
addressed by its explicit calendar id (the service account's own `primary` calendar is empty).
Service accounts cannot invite attendees without domain-wide delegation, so events carry no
attendees; customer details live in the title and description.
"""

import json
import logging
import threading
from collections.abc import Callable
from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import httplib2
from google.auth.exceptions import GoogleAuthError
from google.oauth2 import service_account
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError
from googleapiclient.http import HttpRequest

from app.core.config import Settings
from app.schemas.calendar import CalendarEvent
from app.schemas.time_range import TimeRange
from app.services.errors import CalendarEventNotFoundError, CalendarUnavailableError

logger = logging.getLogger(__name__)

# Least privilege: create, patch, delete and list events (no calendar settings or sharing).
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
# Marks events this app created, so availability ignores them (the bookings table already
# covers them) and only blocks on events someone added by hand.
APP_SOURCE = "ai-appointment-agent"
_MAX_LIST_PAGES = 20
_GONE = (404, 410)


class GoogleCalendar:
    def __init__(
        self,
        calendar_id: str,
        zone: ZoneInfo,
        http_factory: Callable[[], httplib2.Http],
        num_retries: int = 3,
    ) -> None:
        self._calendar_id = calendar_id
        self._zone = zone
        self._http_factory = http_factory
        self._num_retries = num_retries
        self._local = threading.local()

    @classmethod
    def from_settings(cls, settings: Settings) -> "GoogleCalendar":
        calendar_id = settings.google_calendar_id
        key = settings.google_service_account_json
        if calendar_id is None or key is None:
            raise ValueError("GOOGLE_CALENDAR_ID and GOOGLE_SERVICE_ACCOUNT_JSON are required")
        credentials = service_account.Credentials.from_service_account_info(
            json.loads(key.get_secret_value()), scopes=SCOPES
        )
        timeout = settings.calendar_timeout_seconds

        def http_factory() -> httplib2.Http:
            return AuthorizedHttp(credentials, http=httplib2.Http(timeout=timeout))  # type: ignore[return-value]

        return cls(
            calendar_id.get_secret_value(),
            settings.business_zone,
            http_factory,
            settings.calendar_num_retries,
        )

    # ------------------------------------------------------------------ CalendarPort

    def create_event(self, event: CalendarEvent) -> str:
        # The booking id (hex) is the event id: a retried create cannot make a duplicate.
        event_id = event.booking_id.hex
        body = {
            "id": event_id,
            "summary": event.summary,
            "description": event.description,
            "start": self._when(event.start),
            "end": self._when(event.end),
            "extendedProperties": {
                "private": {"booking_id": str(event.booking_id), "source": APP_SOURCE}
            },
        }
        try:
            self._execute(
                "create_event", self._events().insert(calendarId=self._calendar_id, body=body)
            )
        except HttpError as exc:
            if exc.resp.status == 409:  # already created by an earlier attempt
                return event_id
            raise self._unavailable("create_event", exc) from exc
        return event_id

    def update_event(self, event_id: str, start: datetime, end: datetime) -> None:
        body = {"start": self._when(start), "end": self._when(end)}
        try:
            self._execute(
                "update_event",
                self._events().patch(calendarId=self._calendar_id, eventId=event_id, body=body),
            )
        except HttpError as exc:
            if exc.resp.status in _GONE:
                raise CalendarEventNotFoundError(event_id) from exc
            raise self._unavailable("update_event", exc) from exc

    def delete_event(self, event_id: str) -> None:
        try:
            self._execute(
                "delete_event",
                self._events().delete(calendarId=self._calendar_id, eventId=event_id),
            )
        except HttpError as exc:
            if exc.resp.status in _GONE:  # already deleted: the goal is met
                return
            raise self._unavailable("delete_event", exc) from exc

    def list_busy(self, start: datetime, end: datetime) -> list[TimeRange]:
        busy: list[TimeRange] = []
        page_token: str | None = None
        for _ in range(_MAX_LIST_PAGES):
            request = self._events().list(
                calendarId=self._calendar_id,
                timeMin=start.astimezone(UTC).isoformat(),
                timeMax=end.astimezone(UTC).isoformat(),
                singleEvents=True,
                showDeleted=False,
                maxResults=250,
                pageToken=page_token,
                fields=(
                    "nextPageToken,"
                    "items(id,status,transparency,start,end,extendedProperties/private/source)"
                ),
            )
            try:
                page = self._execute("list_busy", request)
            except HttpError as exc:
                raise self._unavailable("list_busy", exc) from exc
            busy.extend(r for item in page.get("items", []) if (r := self._busy_range(item)))
            page_token = page.get("nextPageToken")
            if not page_token:
                return sorted(busy, key=lambda r: r.start)
        # Never answer from a partial view of the calendar.
        logger.error(
            "Google Calendar list_busy returned more than %s pages, failing closed", _MAX_LIST_PAGES
        )
        raise CalendarUnavailableError

    # ---------------------------------------------------------------------- helpers

    def _events(self) -> Resource:
        # httplib2 is not thread-safe and FastAPI runs sync endpoints in a threadpool,
        # so each thread gets its own service object (and HTTP connection).
        service = getattr(self._local, "service", None)
        if service is None:
            service = build(
                "calendar",
                "v3",
                http=self._http_factory(),
                cache_discovery=False,
                static_discovery=True,
            )
            self._local.service = service
        return service.events()

    def _execute(self, operation: str, request: HttpRequest) -> dict[str, Any]:
        try:
            return request.execute(num_retries=self._num_retries)
        except HttpError:
            raise
        except (httplib2.HttpLib2Error, OSError, GoogleAuthError) as exc:
            # Only the error type is logged: messages can contain hosts or token details.
            logger.error(
                "Google Calendar %s failed (%s), failing closed", operation, type(exc).__name__
            )
            raise CalendarUnavailableError from exc

    @staticmethod
    def _unavailable(operation: str, exc: HttpError) -> CalendarUnavailableError:
        status = exc.resp.status
        hint = ""
        if status in (403, 404):
            hint = (
                " (check that the calendar exists and is shared with the service account "
                "with permission 'Make changes to events')"
            )
        elif status == 401:
            hint = " (check the service account key)"
        logger.error("Google Calendar %s failed: HTTP %s %s%s", operation, status, exc.reason, hint)
        return CalendarUnavailableError()

    def _when(self, instant: datetime) -> dict[str, str]:
        return {"dateTime": instant.astimezone(UTC).isoformat(), "timeZone": self._zone.key}

    def _busy_range(self, item: dict[str, Any]) -> TimeRange | None:
        if item.get("status") == "cancelled" or item.get("transparency") == "transparent":
            return None
        if item.get("extendedProperties", {}).get("private", {}).get("source") == APP_SOURCE:
            return None
        return TimeRange(self._instant(item["start"]), self._instant(item["end"]))

    def _instant(self, value: dict[str, str]) -> datetime:
        if "dateTime" in value:
            moment = datetime.fromisoformat(value["dateTime"])
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=ZoneInfo(value.get("timeZone", self._zone.key)))
        else:  # all-day event: spans whole local days in the business timezone
            moment = datetime.combine(
                date.fromisoformat(value["date"]), time.min, tzinfo=self._zone
            )
        return moment.astimezone(UTC)
