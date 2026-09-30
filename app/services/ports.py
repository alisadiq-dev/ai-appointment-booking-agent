"""Repository interfaces the booking service depends on (implemented in app/repositories)."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.schemas.bookings import Booking
from app.schemas.business_hours import BusinessHour
from app.schemas.services import Service
from app.schemas.time_range import TimeRange


class BookingRepositoryPort(Protocol):
    def create(
        self, user_id: UUID, service_id: UUID, start_at: datetime, end_at: datetime
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
