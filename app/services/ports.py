"""Repository interfaces the booking service depends on (implemented in app/repositories)."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.schemas.bookings import Booking
from app.schemas.business_hours import BusinessHour
from app.schemas.calendar import CalendarEvent
from app.schemas.services import Service
from app.schemas.time_range import TimeRange


class BookingRepositoryPort(Protocol):
    def create(
        self,
        user_id: UUID,
        service_id: UUID,
        start_at: datetime,
        end_at: datetime,
        *,
        booking_id: UUID | None = None,
        google_event_id: str | None = None,
    ) -> Booking: ...

    def get_for_user(self, booking_id: UUID, user_id: UUID) -> Booking | None: ...

    def list_for_user(self, user_id: UUID) -> list[Booking]: ...

    def update_times(
        self, booking_id: UUID, user_id: UUID, start_at: datetime, end_at: datetime
    ) -> Booking | None: ...

    def cancel(self, booking_id: UUID, user_id: UUID) -> Booking | None: ...

    def list_busy(self, start: datetime, end: datetime) -> list[TimeRange]: ...

    def admin_list_all(self, limit: int, offset: int) -> list[Booking]: ...


class ServiceRepositoryPort(Protocol):
    def list_all(self) -> list[Service]: ...

    def get(self, service_id: UUID) -> Service | None: ...


class BusinessHoursRepositoryPort(Protocol):
    def list_all(self) -> list[BusinessHour]: ...


class ProfileRepositoryPort(Protocol):
    def get_role(self, user_id: UUID) -> str | None: ...

    def get_full_name(self, user_id: UUID) -> str | None: ...


class CalendarPort(Protocol):
    """The business calendar. Implemented by app/integrations/google_calendar.py.

    Failures raise CalendarUnavailableError (retries already exhausted); nothing is swallowed.
    """

    def create_event(self, event: CalendarEvent) -> str:
        """Create the event and return its id, which MUST be `event.booking_id.hex`.

        The booking row is written with that id before this is called. Idempotent per booking.
        """
        ...

    def update_event(self, event_id: str, start: datetime, end: datetime) -> None:
        """Move an event. Raises CalendarEventNotFoundError if it no longer exists."""
        ...

    def delete_event(self, event_id: str) -> None:
        """Delete an event. Deleting one that is already gone is not an error."""
        ...

    def list_busy(self, start: datetime, end: datetime) -> list[TimeRange]:
        """Busy ranges from events NOT created by this app (manual blocks, holidays, ...)."""
        ...
