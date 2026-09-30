from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from app.repositories.errors import SlotTakenError
from app.schemas.bookings import Booking
from app.schemas.business_hours import BusinessHour
from app.schemas.services import Service
from app.schemas.time_range import TimeRange
from app.services.availability import (
    business_window,
    generate_slots,
    is_aligned,
    window_for_instant,
)
from app.services.errors import (
    BookingInPastError,
    BookingNotActiveError,
    BookingNotFoundError,
    ForbiddenError,
    InvalidSlotTimeError,
    OutsideBusinessHoursError,
    ServiceNotFoundError,
    SlotUnavailableError,
)
from app.services.ports import (
    BookingRepositoryPort,
    BusinessHoursRepositoryPort,
    ProfileRepositoryPort,
    ServiceRepositoryPort,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class BookingService:
    """Booking rules. No HTTP and no SQL here.

    Overlap prevention is the database's job (exclusion constraint): two simultaneous requests
    for one slot cannot both succeed, and the loser surfaces as SlotUnavailableError. This
    service only validates what the database cannot (hours, grid, past) and maps errors.
    """

    def __init__(
        self,
        *,
        bookings: BookingRepositoryPort,
        services: ServiceRepositoryPort,
        business_hours: BusinessHoursRepositoryPort,
        profiles: ProfileRepositoryPort,
        zone: ZoneInfo,
        slot_interval: timedelta,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._bookings = bookings
        self._services = services
        self._business_hours = business_hours
        self._profiles = profiles
        self._zone = zone
        self._interval = slot_interval
        self._clock = clock

    # ------------------------------------------------------------------ read-only

    def list_services(self) -> list[Service]:
        return self._services.list_all()

    def list_business_hours(self) -> list[BusinessHour]:
        return self._business_hours.list_all()

    def get_availability(self, service_id: UUID, day: date) -> list[TimeRange]:
        """Free slots (UTC ranges) for a service on a local calendar day."""
        service = self._get_service(service_id)
        hours = self._business_hours.list_all()
        window = business_window(day, hours, self._zone)
        if window is None:
            return []
        duration = timedelta(minutes=service.duration_minutes)
        starts = generate_slots(
            day=day,
            duration=duration,
            hours=hours,
            zone=self._zone,
            busy=self._bookings.list_busy(window.start, window.end),
            now=self._clock(),
            interval=self._interval,
        )
        return [TimeRange(start, start + duration) for start in starts]

    def list_my_bookings(self, user_id: UUID) -> list[Booking]:
        return self._bookings.list_for_user(user_id)

    def admin_list_bookings(self, actor_id: UUID, limit: int, offset: int) -> list[Booking]:
        if self._profiles.get_role(actor_id) != "admin":
            raise ForbiddenError
        return self._bookings.admin_list_all(limit=limit, offset=offset)

    # --------------------------------------------------------------------- writes

    def create_booking(self, user_id: UUID, service_id: UUID, start_at: datetime) -> Booking:
        service = self._get_service(service_id)
        start, end = self._slot(start_at, service)
        try:
            return self._bookings.create(user_id, service.id, start, end)
        except SlotTakenError as exc:
            raise SlotUnavailableError from exc

    def reschedule_booking(self, user_id: UUID, booking_id: UUID, start_at: datetime) -> Booking:
        booking = self._get_active_future_booking(user_id, booking_id)
        service = self._get_service(booking.service_id)
        start, end = self._slot(start_at, service)
        try:
            updated = self._bookings.update_times(booking.id, user_id, start, end)
        except SlotTakenError as exc:
            raise SlotUnavailableError from exc
        if updated is None:  # cancelled by a concurrent request since we read it
            raise BookingNotActiveError
        return updated

    def cancel_booking(self, user_id: UUID, booking_id: UUID) -> Booking:
        booking = self._get_active_future_booking(user_id, booking_id)
        cancelled = self._bookings.cancel(booking.id, user_id)
        if cancelled is None:  # cancelled by a concurrent request since we read it
            raise BookingNotActiveError
        return cancelled

    # -------------------------------------------------------------------- helpers

    def _get_service(self, service_id: UUID) -> Service:
        service = self._services.get(service_id)
        if service is None:
            raise ServiceNotFoundError
        return service

    def _get_active_future_booking(self, user_id: UUID, booking_id: UUID) -> Booking:
        # Scoped by user_id: someone else's booking is indistinguishable from a missing one.
        booking = self._bookings.get_for_user(booking_id, user_id)
        if booking is None:
            raise BookingNotFoundError
        if booking.status != "confirmed":
            raise BookingNotActiveError
        if booking.start_at <= self._clock():
            raise BookingInPastError("Bookings that have already started cannot be changed.")
        return booking

    def _slot(self, start_at: datetime, service: Service) -> tuple[datetime, datetime]:
        """Validate a requested start for a service; returns the UTC (start, end)."""
        start = start_at.astimezone(UTC)
        end = start + timedelta(minutes=service.duration_minutes)
        if start <= self._clock():
            raise BookingInPastError
        window = window_for_instant(start, self._business_hours.list_all(), self._zone)
        if window is None or start < window.start or end > window.end:
            raise OutsideBusinessHoursError
        if not is_aligned(start, window, self._interval):
            raise InvalidSlotTimeError(int(self._interval.total_seconds() // 60))
        return start, end
