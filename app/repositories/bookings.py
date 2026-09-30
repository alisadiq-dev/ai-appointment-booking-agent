from datetime import UTC, datetime
from uuid import UUID

import psycopg.errors

from app.repositories.errors import SlotTakenError
from app.repositories.types import Conn
from app.schemas.bookings import Booking
from app.schemas.time_range import TimeRange


class BookingRepository:
    """Raw SQL for public.bookings.

    The backend connects with a role that bypasses RLS, so every customer-facing query here
    filters by user_id itself. `list_busy` (no user filter) and `admin_list_all` are the only
    cross-user queries: the first returns bare time ranges with no user data, the second is
    for admins and is authorized in the service layer.

    Writes run in their own transaction block (a savepoint when one is already open), so an
    overlap violation rolls back cleanly and the connection stays usable.
    """

    def __init__(self, conn: Conn) -> None:
        self._conn = conn

    def create(
        self, user_id: UUID, service_id: UUID, start_at: datetime, end_at: datetime
    ) -> Booking:
        try:
            with self._conn.transaction():
                row = self._conn.execute(
                    "insert into public.bookings (user_id, service_id, start_at, end_at) "
                    "values (%s, %s, %s, %s) "
                    "returning id, user_id, service_id, start_at, end_at, status, google_event_id",
                    (user_id, service_id, start_at, end_at),
                ).fetchone()
        except psycopg.errors.ExclusionViolation as exc:
            raise SlotTakenError from exc
        assert row is not None  # noqa: S101 - INSERT ... RETURNING always yields a row
        return Booking.model_validate(row)

    def get_for_user(self, booking_id: UUID, user_id: UUID) -> Booking | None:
        row = self._conn.execute(
            "select id, user_id, service_id, start_at, end_at, status, google_event_id "
            "from public.bookings where id = %s and user_id = %s",
            (booking_id, user_id),
        ).fetchone()
        return Booking.model_validate(row) if row else None

    def list_for_user(self, user_id: UUID) -> list[Booking]:
        rows = self._conn.execute(
            "select id, user_id, service_id, start_at, end_at, status, google_event_id "
            "from public.bookings where user_id = %s order by start_at desc",
            (user_id,),
        ).fetchall()
        return [Booking.model_validate(r) for r in rows]

    def update_times(
        self, booking_id: UUID, user_id: UUID, start_at: datetime, end_at: datetime
    ) -> Booking | None:
        """Move a confirmed booking in place. None if it is not the user's or not confirmed."""
        try:
            with self._conn.transaction():
                row = self._conn.execute(
                    "update public.bookings set start_at = %s, end_at = %s "
                    "where id = %s and user_id = %s and status = 'confirmed' "
                    "returning id, user_id, service_id, start_at, end_at, status, google_event_id",
                    (start_at, end_at, booking_id, user_id),
                ).fetchone()
        except psycopg.errors.ExclusionViolation as exc:
            raise SlotTakenError from exc
        return Booking.model_validate(row) if row else None

    def cancel(self, booking_id: UUID, user_id: UUID) -> Booking | None:
        """Mark a confirmed booking cancelled (row kept). None if not theirs or not confirmed."""
        with self._conn.transaction():
            row = self._conn.execute(
                "update public.bookings set status = 'cancelled' "
                "where id = %s and user_id = %s and status = 'confirmed' "
                "returning id, user_id, service_id, start_at, end_at, status, google_event_id",
                (booking_id, user_id),
            ).fetchone()
        return Booking.model_validate(row) if row else None

    def list_busy(self, start: datetime, end: datetime) -> list[TimeRange]:
        """Confirmed time ranges overlapping [start, end). Deliberately carries no user data."""
        rows = self._conn.execute(
            "select start_at, end_at from public.bookings "
            "where status = 'confirmed' "
            "and tstzrange(start_at, end_at, '[)') && tstzrange(%s, %s, '[)') "
            "order by start_at",
            (start, end),
        ).fetchall()
        return [TimeRange(r["start_at"].astimezone(UTC), r["end_at"].astimezone(UTC)) for r in rows]

    def admin_list_all(self, limit: int, offset: int) -> list[Booking]:
        """All users' bookings, newest first. Callers must have checked admin rights."""
        rows = self._conn.execute(
            "select id, user_id, service_id, start_at, end_at, status, google_event_id "
            "from public.bookings order by start_at desc, id limit %s offset %s",
            (limit, offset),
        ).fetchall()
        return [Booking.model_validate(r) for r in rows]
