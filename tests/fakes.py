"""In-memory repositories that behave like the real ones, including the overlap rule."""

import uuid
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.repositories.errors import SlotTakenError
from app.schemas.bookings import Booking
from app.schemas.business_hours import BusinessHour
from app.schemas.services import Service
from app.schemas.time_range import TimeRange


class InMemoryBookingRepository:
    def __init__(self) -> None:
        self.rows: dict[UUID, Booking] = {}

    def _conflicts(self, start: datetime, end: datetime, ignore: UUID | None = None) -> bool:
        return any(
            b.status == "confirmed" and b.id != ignore and b.start_at < end and start < b.end_at
            for b in self.rows.values()
        )

    def create(
        self, user_id: UUID, service_id: UUID, start_at: datetime, end_at: datetime
    ) -> Booking:
        if self._conflicts(start_at, end_at):
            raise SlotTakenError
        booking = Booking(
            id=uuid.uuid4(),
            user_id=user_id,
            service_id=service_id,
            start_at=start_at,
            end_at=end_at,
            status="confirmed",
            google_event_id=None,
        )
        self.rows[booking.id] = booking
        return booking

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
    def __init__(self, roles: dict[UUID, str]) -> None:
        self._roles = roles

    def get_role(self, user_id: UUID) -> str | None:
        return self._roles.get(user_id)


def make_service(name: str = "Haircut", minutes: int = 30) -> Service:
    return Service(id=uuid.uuid4(), name=name, duration_minutes=minutes, price=Decimal("25.00"))
