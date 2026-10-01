from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg.errors
import pytest

from app.repositories.bookings import BookingRepository
from app.repositories.business_hours import BusinessHoursRepository
from app.repositories.errors import SlotTakenError
from app.repositories.profiles import ProfileRepository
from app.repositories.services import ServiceRepository
from app.schemas.time_range import TimeRange
from tests.integration.conftest import Conn, make_service, make_user, requires_db

pytestmark = [pytest.mark.local_supabase, requires_db]


def at(hour: int, minute: int = 0, day: int = 7) -> datetime:
    return datetime(2030, 1, day, hour, minute, tzinfo=UTC)


def book(repo: BookingRepository, user, service, start: datetime, minutes: int = 30):  # type: ignore[no-untyped-def]
    return repo.create(user, service, start, start + timedelta(minutes=minutes))


# ----------------------------------------------------------------- bookings: create / read


def test_create_returns_a_confirmed_utc_booking(conn: Conn) -> None:
    repo = BookingRepository(conn)
    user, service = make_user(conn), make_service(conn)

    booking = book(repo, user, service, at(10))

    assert booking.user_id == user
    assert booking.service_id == service
    assert booking.status == "confirmed"
    assert booking.google_event_id is None
    assert booking.start_at == at(10)
    assert booking.end_at == at(10, 30)
    assert booking.start_at.utcoffset() == timedelta(0)


def test_create_can_use_a_caller_chosen_id_and_google_event_id(conn: Conn) -> None:
    repo = BookingRepository(conn)
    user, service = make_user(conn), make_service(conn)
    booking_id = uuid4()

    booking = repo.create(
        user,
        service,
        at(10),
        at(10, 30),
        booking_id=booking_id,
        google_event_id=booking_id.hex,
    )

    assert booking.id == booking_id
    assert booking.google_event_id == booking_id.hex
    stored = repo.get_for_user(booking_id, user)
    assert stored is not None
    assert stored.google_event_id == booking_id.hex


def test_two_bookings_cannot_share_a_google_event_id(conn: Conn) -> None:
    repo = BookingRepository(conn)
    user, service = make_user(conn), make_service(conn)
    repo.create(user, service, at(10), at(10, 30), google_event_id="shared-event-id")

    with pytest.raises(psycopg.errors.UniqueViolation):
        repo.create(user, service, at(12), at(12, 30), google_event_id="shared-event-id")


def test_get_for_user_returns_only_the_owners_booking(conn: Conn) -> None:
    repo = BookingRepository(conn)
    alice, bob, service = make_user(conn), make_user(conn), make_service(conn)
    booking = book(repo, alice, service, at(10))

    assert repo.get_for_user(booking.id, alice) == booking
    assert repo.get_for_user(booking.id, bob) is None
    assert repo.get_for_user(uuid4(), alice) is None


def test_list_for_user_returns_only_own_bookings_newest_first(conn: Conn) -> None:
    repo = BookingRepository(conn)
    alice, bob, service = make_user(conn), make_user(conn), make_service(conn)
    early = book(repo, alice, service, at(9))
    late = book(repo, alice, service, at(11))
    book(repo, bob, service, at(14))

    assert [b.id for b in repo.list_for_user(alice)] == [late.id, early.id]
    assert [b.user_id for b in repo.list_for_user(bob)] == [bob]
    assert repo.list_for_user(make_user(conn)) == []


# ------------------------------------------------------------- bookings: update / cancel


def test_update_times_changes_own_booking_in_place(conn: Conn) -> None:
    repo = BookingRepository(conn)
    user, service = make_user(conn), make_service(conn)
    booking = book(repo, user, service, at(10))

    updated = repo.update_times(booking.id, user, at(15), at(15, 30))

    assert updated is not None
    assert updated.id == booking.id
    assert (updated.start_at, updated.end_at) == (at(15), at(15, 30))


def test_update_times_cannot_touch_another_users_booking(conn: Conn) -> None:
    repo = BookingRepository(conn)
    alice, bob, service = make_user(conn), make_user(conn), make_service(conn)
    booking = book(repo, alice, service, at(10))

    assert repo.update_times(booking.id, bob, at(15), at(15, 30)) is None
    assert repo.get_for_user(booking.id, alice) == booking  # unchanged


def test_update_times_ignores_cancelled_bookings(conn: Conn) -> None:
    repo = BookingRepository(conn)
    user, service = make_user(conn), make_service(conn)
    booking = book(repo, user, service, at(10))
    repo.cancel(booking.id, user)

    assert repo.update_times(booking.id, user, at(15), at(15, 30)) is None


def test_cancel_marks_own_booking_cancelled_and_keeps_the_row(conn: Conn) -> None:
    repo = BookingRepository(conn)
    user, service = make_user(conn), make_service(conn)
    booking = book(repo, user, service, at(10))

    cancelled = repo.cancel(booking.id, user)

    assert cancelled is not None
    assert cancelled.status == "cancelled"
    assert repo.get_for_user(booking.id, user) is not None


def test_cancel_cannot_touch_another_users_booking(conn: Conn) -> None:
    repo = BookingRepository(conn)
    alice, bob, service = make_user(conn), make_user(conn), make_service(conn)
    booking = book(repo, alice, service, at(10))

    assert repo.cancel(booking.id, bob) is None
    stored = repo.get_for_user(booking.id, alice)
    assert stored is not None
    assert stored.status == "confirmed"


def test_cancelling_twice_returns_none_the_second_time(conn: Conn) -> None:
    repo = BookingRepository(conn)
    user, service = make_user(conn), make_service(conn)
    booking = book(repo, user, service, at(10))
    repo.cancel(booking.id, user)

    assert repo.cancel(booking.id, user) is None


# --------------------------------------------------------------- bookings: overlap rule


def test_overlapping_create_raises_slot_taken_and_the_connection_stays_usable(conn: Conn) -> None:
    repo = BookingRepository(conn)
    alice, bob, service = make_user(conn), make_user(conn), make_service(conn)
    book(repo, alice, service, at(10))

    with pytest.raises(SlotTakenError):
        book(repo, bob, service, at(10, 15))

    assert repo.list_for_user(alice)  # no "transaction aborted" error: a savepoint was used


def test_back_to_back_bookings_are_allowed(conn: Conn) -> None:
    repo = BookingRepository(conn)
    user, service = make_user(conn), make_service(conn)
    book(repo, user, service, at(10))

    assert book(repo, user, service, at(10, 30)).start_at == at(10, 30)


def test_update_times_onto_its_own_overlapping_range_succeeds(conn: Conn) -> None:
    repo = BookingRepository(conn)
    user, service = make_user(conn), make_service(conn)
    booking = book(repo, user, service, at(10))

    updated = repo.update_times(booking.id, user, at(10, 15), at(10, 45))

    assert updated is not None
    assert updated.start_at == at(10, 15)


def test_update_times_onto_another_bookings_slot_raises_slot_taken(conn: Conn) -> None:
    repo = BookingRepository(conn)
    alice, bob, service = make_user(conn), make_user(conn), make_service(conn)
    book(repo, alice, service, at(10))
    bobs = book(repo, bob, service, at(11))

    with pytest.raises(SlotTakenError):
        repo.update_times(bobs.id, bob, at(10, 15), at(10, 45))

    stored = repo.get_for_user(bobs.id, bob)
    assert stored is not None
    assert stored.start_at == at(11)


def test_a_cancelled_booking_frees_its_slot(conn: Conn) -> None:
    repo = BookingRepository(conn)
    alice, bob, service = make_user(conn), make_user(conn), make_service(conn)
    first = book(repo, alice, service, at(10))
    repo.cancel(first.id, alice)

    assert book(repo, bob, service, at(10)).user_id == bob


# ---------------------------------------------------------------- bookings: busy / admin


def test_list_busy_returns_only_confirmed_overlapping_ranges_without_user_data(
    conn: Conn,
) -> None:
    repo = BookingRepository(conn)
    alice, service = make_user(conn), make_service(conn)
    book(repo, alice, service, at(9))
    cancelled = book(repo, alice, service, at(10))
    repo.cancel(cancelled.id, alice)
    book(repo, alice, service, at(11))
    book(repo, alice, service, at(14))

    busy = repo.list_busy(at(9, 15), at(11, 30))

    assert busy == [TimeRange(at(9), at(9, 30)), TimeRange(at(11), at(11, 30))]


def test_list_busy_treats_ranges_as_half_open(conn: Conn) -> None:
    repo = BookingRepository(conn)
    user, service = make_user(conn), make_service(conn)
    book(repo, user, service, at(10))

    assert repo.list_busy(at(10, 30), at(11)) == []  # starts exactly when the booking ends
    assert repo.list_busy(at(9, 30), at(10)) == []  # ends exactly when the booking starts


def test_admin_list_all_spans_users_and_paginates(conn: Conn) -> None:
    repo = BookingRepository(conn)
    alice, bob, service = make_user(conn), make_user(conn), make_service(conn)
    first = book(repo, alice, service, at(9, day=8))
    second = book(repo, bob, service, at(10, day=8))
    third = book(repo, alice, service, at(11, day=8))

    everything = [b.id for b in repo.admin_list_all(limit=100, offset=0) if b.start_at.day == 8]
    assert everything == [third.id, second.id, first.id]
    assert [b.id for b in repo.admin_list_all(limit=1, offset=0)] == [third.id]
    assert len(repo.admin_list_all(limit=1, offset=1)) == 1


# ------------------------------------------------------------------ other repositories


def test_service_repository_lists_and_gets_services(conn: Conn) -> None:
    repo = ServiceRepository(conn)
    service_id = make_service(conn, minutes=45, name="zz-repo-test")

    found = repo.get(service_id)

    assert found is not None
    assert (found.name, found.duration_minutes) == ("zz-repo-test", 45)
    assert "zz-repo-test" in [s.name for s in repo.list_all()]
    assert "Haircut" in [s.name for s in repo.list_all()]  # seeded
    assert repo.get(uuid4()) is None


def test_business_hours_repository_returns_the_seeded_week_in_order(conn: Conn) -> None:
    hours = BusinessHoursRepository(conn).list_all()

    assert [h.day_of_week for h in hours] == [1, 2, 3, 4, 5, 6, 7]
    assert hours[6].open_time is None  # Sunday closed
    assert hours[0].open_time is not None


def test_profile_repository_returns_the_full_name_or_none(conn: Conn) -> None:
    repo = ProfileRepository(conn)
    named = make_user(conn, full_name="  Alice Khan ")
    unnamed = make_user(conn)
    blank = make_user(conn, full_name="   ")

    assert repo.get_full_name(named) == "Alice Khan"
    assert repo.get_full_name(unnamed) is None
    assert repo.get_full_name(blank) is None
    assert repo.get_full_name(uuid4()) is None


def test_profile_repository_returns_roles(conn: Conn) -> None:
    repo = ProfileRepository(conn)
    customer = make_user(conn)
    admin = make_user(conn, role="admin")

    assert repo.get_role(customer) == "customer"
    assert repo.get_role(admin) == "admin"
    assert repo.get_role(uuid4()) is None
