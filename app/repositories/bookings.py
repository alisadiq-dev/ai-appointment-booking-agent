from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import psycopg.errors

from app.repositories.errors import ActiveBookingLimitError, SlotTakenError
from app.repositories.types import Conn
from app.schemas.bookings import Booking
from app.schemas.time_range import TimeRange

# Arbitrary constant key for the transaction-scoped advisory lock that serializes booking
# writes. There is one business calendar and write volume is tiny, so this costs nothing, and
# it turns simultaneous conflicting writes into an orderly queue: the loser waits for the
# winner to commit, then fails cleanly with exclusion_violation instead of deadlocking (a
# deadlock costs Postgres' 1 s detection timeout). The exclusion constraint remains the rule.
BOOKING_WRITE_LOCK_KEY = 7_243_001


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

    def _write_returning_row(self, query: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        """Run a booking write in its own transaction block and return the resulting row.

        Writes queue on an advisory lock (see BOOKING_WRITE_LOCK_KEY), so conflicts normally
        surface as exclusion_violation (23P01). As a safety net, if Postgres still aborts one
        with deadlock_detected (40P01), retry once (the retry then sees the winner's row and
        fails with 23P01, or succeeds if the winner rolled back) and report SlotTakenError.
        Nobody is double-booked either way; the loser must never see a 500.
        """
        for attempt in (1, 2):
            try:
                with self._conn.transaction():
                    self._conn.execute(
                        "select pg_advisory_xact_lock(%s)", (BOOKING_WRITE_LOCK_KEY,)
                    )
                    return self._conn.execute(query, params).fetchone()
            except psycopg.errors.ExclusionViolation as exc:
                raise SlotTakenError from exc
            except psycopg.errors.DeadlockDetected as exc:
                if attempt == 2:
                    raise SlotTakenError from exc
        raise AssertionError("unreachable")  # pragma: no cover

    def create(
        self,
        user_id: UUID,
        service_id: UUID,
        start_at: datetime,
        end_at: datetime,
        *,
        booking_id: UUID | None = None,
        google_event_id: str | None = None,
        max_active: int | None = None,
        now: datetime | None = None,
    ) -> Booking:
        # The limit is counted inside the same statement, under the booking write lock, so two
        # simultaneous requests from one user cannot both pass it. No row back = limit reached.
        row = self._write_returning_row(
            "insert into public.bookings "
            "(id, user_id, service_id, start_at, end_at, google_event_id) "
            "select coalesce(%s::uuid, gen_random_uuid()), %s, %s, %s, %s, %s "
            "where %s::int is null or (select count(*) from public.bookings "
            "  where user_id = %s and status = 'confirmed' "
            "  and start_at > %s::timestamptz) < %s::int "
            "returning id, user_id, service_id, start_at, end_at, status, google_event_id",
            (
                booking_id,
                user_id,
                service_id,
                start_at,
                end_at,
                google_event_id,
                max_active,
                user_id,
                now,
                max_active,
            ),
        )
        if row is None:
            raise ActiveBookingLimitError
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
        row = self._write_returning_row(
            "update public.bookings set start_at = %s, end_at = %s "
            "where id = %s and user_id = %s and status = 'confirmed' "
            "returning id, user_id, service_id, start_at, end_at, status, google_event_id",
            (start_at, end_at, booking_id, user_id),
        )
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
