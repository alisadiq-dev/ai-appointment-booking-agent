"""In-memory repositories that behave like the real ones, including the overlap rule."""

import uuid
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.repositories.errors import SlotTakenError
from app.schemas.bookings import Booking
from app.schemas.business_hours import BusinessHour
from app.schemas.calendar import CalendarEvent
from app.schemas.services import Service
from app.schemas.time_range import TimeRange
from app.services.errors import CalendarEventNotFoundError, CalendarUnavailableError


class InMemoryBookingRepository:
    def __init__(self) -> None:
        self.rows: dict[UUID, Booking] = {}

    def _conflicts(self, start: datetime, end: datetime, ignore: UUID | None = None) -> bool:
        return any(
            b.status == "confirmed" and b.id != ignore and b.start_at < end and start < b.end_at
            for b in self.rows.values()
        )

    def create(
        self,
        user_id: UUID,
        service_id: UUID,
        start_at: datetime,
        end_at: datetime,
        *,
        booking_id: UUID | None = None,
        google_event_id: str | None = None,
    ) -> Booking:
        if self._conflicts(start_at, end_at):
            raise SlotTakenError
        booking = Booking(
            id=booking_id or uuid.uuid4(),
            user_id=user_id,
            service_id=service_id,
            start_at=start_at,
            end_at=end_at,
            status="confirmed",
            google_event_id=google_event_id,
        )
        self.rows[booking.id] = booking
        return booking

    def snapshot(self) -> dict[UUID, Booking]:
        """Emulates the start of the request transaction (see Harness.request)."""
        return dict(self.rows)

    def restore(self, snapshot: dict[UUID, Booking]) -> None:
        """Emulates the request transaction rolling back."""
        self.rows = dict(snapshot)

    def get_for_user(self, booking_id: UUID, user_id: UUID) -> Booking | None:
        booking = self.rows.get(booking_id)
        return booking if booking and booking.user_id == user_id else None

    def list_for_user(self, user_id: UUID) -> list[Booking]:
        mine = [b for b in self.rows.values() if b.user_id == user_id]
        return sorted(mine, key=lambda b: b.start_at, reverse=True)

    def update_times(
        self, booking_id: UUID, user_id: UUID, start_at: datetime, end_at: datetime
    ) -> Booking | None:
        booking = self.get_for_user(booking_id, user_id)
        if booking is None or booking.status != "confirmed":
            return None
        if self._conflicts(start_at, end_at, ignore=booking_id):
            raise SlotTakenError
        updated = booking.model_copy(update={"start_at": start_at, "end_at": end_at})
        self.rows[booking_id] = updated
        return updated

    def cancel(self, booking_id: UUID, user_id: UUID) -> Booking | None:
        booking = self.get_for_user(booking_id, user_id)
        if booking is None or booking.status != "confirmed":
            return None
        cancelled = booking.model_copy(update={"status": "cancelled"})
        self.rows[booking_id] = cancelled
        return cancelled

    def list_busy(self, start: datetime, end: datetime) -> list[TimeRange]:
        return sorted(
            (
                TimeRange(b.start_at, b.end_at)
                for b in self.rows.values()
                if b.status == "confirmed" and b.start_at < end and start < b.end_at
            ),
            key=lambda r: r.start,
        )

    def admin_list_all(self, limit: int, offset: int) -> list[Booking]:
        everything = sorted(self.rows.values(), key=lambda b: b.start_at, reverse=True)
        return everything[offset : offset + limit]


class InMemoryServiceRepository:
    def __init__(self, services: list[Service]) -> None:
        self._services = {s.id: s for s in services}

    def list_all(self) -> list[Service]:
        return sorted(self._services.values(), key=lambda s: s.name)

    def get(self, service_id: UUID) -> Service | None:
        return self._services.get(service_id)


class InMemoryBusinessHoursRepository:
    def __init__(self, hours: list[BusinessHour]) -> None:
        self._hours = hours

    def list_all(self) -> list[BusinessHour]:
        return sorted(self._hours, key=lambda h: h.day_of_week)


class InMemoryProfileRepository:
    def __init__(self, roles: dict[UUID, str], names: dict[UUID, str | None] | None = None) -> None:
        self._roles = roles
        self._names = names or {}

    def get_role(self, user_id: UUID) -> str | None:
        return self._roles.get(user_id)

    def get_full_name(self, user_id: UUID) -> str | None:
        return self._names.get(user_id)


class FakeCalendar:
    """Records calls and can simulate Google failures, missing events and manual busy time."""

    def __init__(self) -> None:
        self.events: dict[str, CalendarEvent] = {}
        self.manual_busy: list[TimeRange] = []
        self.failing: set[str] = set()  # operation names that raise CalendarUnavailableError
        self.gone: set[str] = set()  # event ids deleted by hand: updates raise not-found
        self.calls: list[str] = []

    def _enter(self, operation: str) -> None:
        self.calls.append(operation)
        if operation in self.failing:
            raise CalendarUnavailableError

    def create_event(self, event: CalendarEvent) -> str:
        self._enter("create_event")
        self.events[event.booking_id.hex] = event
        return event.booking_id.hex

    def update_event(self, event_id: str, start: datetime, end: datetime) -> None:
        self._enter("update_event")
        event = self.events.get(event_id)
        if event is None or event_id in self.gone:
            raise CalendarEventNotFoundError(event_id)
        self.events[event_id] = CalendarEvent(
            event.booking_id, event.summary, event.description, start, end
        )

    def delete_event(self, event_id: str) -> None:
        self._enter("delete_event")
        self.events.pop(event_id, None)

    def list_busy(self, start: datetime, end: datetime) -> list[TimeRange]:
        self._enter("list_busy")
        return [r for r in self.manual_busy if r.overlaps(start, end)]


def make_service(name: str = "Haircut", minutes: int = 30) -> Service:
    return Service(id=uuid.uuid4(), name=name, duration_minutes=minutes, price=Decimal("25.00"))
