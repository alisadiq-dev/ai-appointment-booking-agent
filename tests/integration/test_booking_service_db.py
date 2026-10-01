"""BookingService wired to the real repositories and Postgres.

These prove what fakes cannot: per-user isolation in SQL, the overlap rule as enforced by the
database exclusion constraint, and correct behaviour under concurrent requests.
"""

import threading
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg.rows import dict_row

from app.repositories.bookings import BookingRepository
from app.repositories.business_hours import BusinessHoursRepository
from app.repositories.profiles import ProfileRepository
from app.repositories.services import ServiceRepository
from app.services.booking_service import BookingService
from app.services.errors import (
    BookingLimitReachedError,
    BookingNotActiveError,
    BookingNotFoundError,
    ForbiddenError,
    SlotUnavailableError,
)
from tests.fakes import FakeCalendar
from tests.integration.conftest import DATABASE_URL, Conn, make_user, requires_db

pytestmark = [pytest.mark.local_supabase, requires_db]

NOW = datetime(2030, 1, 1, tzinfo=UTC)


def at(hour: int, minute: int = 0, day: int = 7) -> datetime:
    """2030-01-07 is a Monday; Karachi (UTC+5) is open 04:00Z-13:00Z."""
    return datetime(2030, 1, day, hour, minute, tzinfo=UTC)


def build_service(conn: Conn) -> BookingService:
    return BookingService(
        bookings=BookingRepository(conn),
        services=ServiceRepository(conn),
        business_hours=BusinessHoursRepository(conn),
        profiles=ProfileRepository(conn),
        calendar=FakeCalendar(),
        zone=ZoneInfo("Asia/Karachi"),
        slot_interval=timedelta(minutes=15),
        clock=lambda: NOW,
    )


def seeded_service_id(conn: Conn, name: str = "Haircut") -> UUID:
    service = next(s for s in ServiceRepository(conn).list_all() if s.name == name)
    return service.id


# ------------------------------------------------------------- user isolation (required)


def test_user_a_cannot_read_user_b_bookings(conn: Conn) -> None:
    svc = build_service(conn)
    haircut = seeded_service_id(conn)
    alice, bob = make_user(conn), make_user(conn)
    alices = svc.create_booking(alice, haircut, at(5))

    assert svc.list_my_bookings(bob) == []
    assert [b.id for b in svc.list_my_bookings(alice)] == [alices.id]


def test_user_b_cannot_reschedule_or_cancel_user_a_booking(conn: Conn) -> None:
    svc = build_service(conn)
    haircut = seeded_service_id(conn)
    alice, bob = make_user(conn), make_user(conn)
    alices = svc.create_booking(alice, haircut, at(5))

    with pytest.raises(BookingNotFoundError):
        svc.reschedule_booking(bob, alices.id, at(7))
    with pytest.raises(BookingNotFoundError):
        svc.cancel_booking(bob, alices.id)

    [stored] = svc.list_my_bookings(alice)
    assert (stored.status, stored.start_at) == ("confirmed", at(5))


# ---------------------------------------------------------------- reschedule (required)


def test_reschedule_to_a_time_overlapping_itself_succeeds(conn: Conn) -> None:
    svc = build_service(conn)
    user = make_user(conn)
    booking = svc.create_booking(user, seeded_service_id(conn), at(5))

    moved = svc.reschedule_booking(user, booking.id, at(5, 15))

    assert moved.id == booking.id
    assert (moved.start_at, moved.end_at) == (at(5, 15), at(5, 45))


def test_reschedule_onto_another_bookings_time_fails(conn: Conn) -> None:
    svc = build_service(conn)
    haircut = seeded_service_id(conn)
    alice, bob = make_user(conn), make_user(conn)
    svc.create_booking(alice, haircut, at(5))
    bobs = svc.create_booking(bob, haircut, at(7))

    with pytest.raises(SlotUnavailableError):
        svc.reschedule_booking(bob, bobs.id, at(5, 15))

    assert svc.list_my_bookings(bob)[0].start_at == at(7)  # unchanged, connection still usable


def test_reschedule_keeps_the_same_row_and_google_event_id(conn: Conn) -> None:
    svc = build_service(conn)
    user = make_user(conn)
    booking = svc.create_booking(user, seeded_service_id(conn), at(5))
    assert booking.google_event_id == booking.id.hex  # assigned at creation

    moved = svc.reschedule_booking(user, booking.id, at(9))

    assert moved.id == booking.id
    assert moved.google_event_id == booking.google_event_id


def test_reschedule_a_cancelled_booking_is_not_active(conn: Conn) -> None:
    svc = build_service(conn)
    user = make_user(conn)
    booking = svc.create_booking(user, seeded_service_id(conn), at(5))
    svc.cancel_booking(user, booking.id)

    with pytest.raises(BookingNotActiveError):
        svc.reschedule_booking(user, booking.id, at(9))


# ------------------------------------------------------------------- overlap, end to end


def test_overlapping_booking_is_rejected_by_the_database(conn: Conn) -> None:
    svc = build_service(conn)
    haircut = seeded_service_id(conn)
    svc.create_booking(make_user(conn), haircut, at(5))

    with pytest.raises(SlotUnavailableError):
        svc.create_booking(make_user(conn), haircut, at(5, 15))


def test_cancel_frees_the_slot_and_availability_reflects_it(conn: Conn) -> None:
    svc = build_service(conn)
    haircut = seeded_service_id(conn)
    alice, bob = make_user(conn), make_user(conn)
    booking = svc.create_booking(alice, haircut, at(5))
    local_monday = datetime(2030, 1, 7).date()

    assert at(5) not in [s.start for s in svc.get_availability(haircut, local_monday)]

    svc.cancel_booking(alice, booking.id)

    assert at(5) in [s.start for s in svc.get_availability(haircut, local_monday)]
    assert svc.create_booking(bob, haircut, at(5)).user_id == bob


def test_admin_listing_needs_the_admin_role(conn: Conn) -> None:
    svc = build_service(conn)
    haircut = seeded_service_id(conn)
    customer, admin = make_user(conn), make_user(conn, role="admin")
    svc.create_booking(customer, haircut, at(5))

    assert any(b.user_id == customer for b in svc.admin_list_bookings(admin, 100, 0))
    with pytest.raises(ForbiddenError):
        svc.admin_list_bookings(customer, 100, 0)


# --------------------------------------------------------------------------- concurrency


def test_concurrent_requests_for_one_slot_have_exactly_one_winner() -> None:
    """Separate connections, committed data, simultaneous inserts. The DB must pick one.

    Several rounds on different slots: Postgres sometimes resolves the race with a deadlock
    abort instead of an exclusion violation, and the repository must handle both.
    """
    assert DATABASE_URL
    contenders, rounds = 6, 8
    setup: psycopg.Connection[dict[str, Any]] = psycopg.connect(
        DATABASE_URL, row_factory=dict_row, autocommit=True
    )
    users = [make_user(setup) for _ in range(contenders)]  # autocommit: rows are committed
    haircut = seeded_service_id(setup)
    errors: list[BaseException] = []

    def run_round(start: datetime) -> list[str]:
        barrier = threading.Barrier(contenders)
        results: list[str] = []

        def attempt(user: UUID) -> None:
            connection: psycopg.Connection[dict[str, Any]] = psycopg.connect(
                DATABASE_URL, row_factory=dict_row
            )
            try:
                svc = build_service(connection)
                barrier.wait()
                try:
                    svc.create_booking(user, haircut, start)
                    connection.commit()
                    results.append("booked")
                except SlotUnavailableError:
                    connection.rollback()
                    results.append("taken")
            except BaseException as exc:
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=attempt, args=(u,)) for u in users]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        return results

    try:
        for i in range(rounds):  # 30-minute slots on one open Monday (2030-01-14)
            start = at(4, 0, day=14) + timedelta(minutes=30 * i)
            assert sorted(run_round(start)) == ["booked"] + ["taken"] * (contenders - 1)
        assert errors == []  # no 500-style failures (e.g. an unhandled deadlock)
    finally:
        setup.execute("delete from auth.users where id = any(%s)", (users,))  # cascades
        setup.close()


# ------------------------------------------------------------- active booking limit


def test_the_limit_is_enforced_in_sql_and_reschedule_and_cancel_still_work(conn: Conn) -> None:
    svc = build_service(conn)
    haircut = seeded_service_id(conn)
    alice, bob = make_user(conn), make_user(conn)
    mine = [svc.create_booking(alice, haircut, at(5 + i)) for i in range(3)]

    with pytest.raises(BookingLimitReachedError):
        svc.create_booking(alice, haircut, at(9))
    assert len(svc.list_my_bookings(alice)) == 3  # the refused one left no row

    moved = svc.reschedule_booking(alice, mine[0].id, at(9))  # not a new booking
    assert moved.id == mine[0].id
    svc.create_booking(bob, haircut, at(10))  # another user is unaffected
    svc.cancel_booking(alice, mine[1].id)
    assert svc.create_booking(alice, haircut, at(11)).status == "confirmed"  # a place was freed


def test_cancelled_and_past_bookings_do_not_count_in_sql(conn: Conn) -> None:
    svc = build_service(conn)
    haircut = seeded_service_id(conn)
    alice = make_user(conn)
    for i in range(3):
        svc.cancel_booking(alice, svc.create_booking(alice, haircut, at(5 + i)).id)
    # a booking that has already started (inserted directly: the service refuses past times)
    conn.execute(
        "insert into public.bookings (user_id, service_id, start_at, end_at) "
        "values (%s, %s, %s, %s)",
        (
            alice,
            haircut,
            datetime(2029, 12, 1, 5, tzinfo=UTC),
            datetime(2029, 12, 1, 6, tzinfo=UTC),
        ),
    )

    for i in range(3):
        svc.create_booking(alice, haircut, at(5 + i, day=8))  # still room for three


def test_concurrent_bookings_by_one_user_cannot_exceed_the_limit() -> None:
    """Separate connections, committed data, one user booking 8 different slots at once."""
    assert DATABASE_URL
    attempts = 8
    setup: psycopg.Connection[dict[str, Any]] = psycopg.connect(
        DATABASE_URL, row_factory=dict_row, autocommit=True
    )
    user = make_user(setup)
    haircut = seeded_service_id(setup)
    barrier = threading.Barrier(attempts)
    results: list[str] = []
    errors: list[BaseException] = []

    def attempt(i: int) -> None:
        connection: psycopg.Connection[dict[str, Any]] = psycopg.connect(
            DATABASE_URL, row_factory=dict_row
        )
        try:
            svc = build_service(connection)
            barrier.wait()
            try:
                svc.create_booking(user, haircut, at(4, 0, day=21) + timedelta(hours=i))
                connection.commit()
                results.append("booked")
            except BookingLimitReachedError:
                connection.rollback()
                results.append("limit")
        except BaseException as exc:
            errors.append(exc)
        finally:
            connection.close()

    try:
        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(attempts)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        stored = setup.execute(
            "select count(*) as n from public.bookings where user_id = %s", (user,)
        ).fetchone()
        assert errors == []
        assert sorted(results) == ["booked"] * 3 + ["limit"] * (attempts - 3)
        assert stored is not None
        assert stored["n"] == 3
    finally:
        setup.execute("delete from auth.users where id = %s", (user,))  # cascades
        setup.close()
