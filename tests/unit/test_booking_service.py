import uuid
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.repositories.errors import SlotTakenError
from app.schemas.bookings import Booking
from app.schemas.business_hours import BusinessHour
from app.schemas.services import Service
from app.schemas.time_range import TimeRange
from app.services.booking_service import BookingService
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
from tests.fakes import (
    InMemoryBookingRepository,
    InMemoryBusinessHoursRepository,
    InMemoryProfileRepository,
    InMemoryServiceRepository,
    make_service,
)

KARACHI = ZoneInfo("Asia/Karachi")  # UTC+5
NOW = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
MONDAY = date(2026, 10, 5)  # open 09:00-18:00 local = 04:00Z-13:00Z
SUNDAY = date(2026, 10, 4)  # closed

ALICE = uuid.uuid4()
BOB = uuid.uuid4()
ADMIN = uuid.uuid4()


def monday(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 5, hour, minute, tzinfo=UTC)


def _hours() -> list[BusinessHour]:
    rows = [
        BusinessHour(day_of_week=d, open_time=time(9), close_time=time(18)) for d in range(1, 6)
    ]
    rows.append(BusinessHour(day_of_week=6, open_time=time(10), close_time=time(16)))
    rows.append(BusinessHour(day_of_week=7, open_time=None, close_time=None))
    return rows


class Harness:
    def __init__(self) -> None:
        self.haircut: Service = make_service("Haircut", 30)
        self.combo: Service = make_service("Haircut & Beard", 45)
        self.bookings = InMemoryBookingRepository()
        self.now = NOW
        self.service = BookingService(
            bookings=self.bookings,
            services=InMemoryServiceRepository([self.haircut, self.combo]),
            business_hours=InMemoryBusinessHoursRepository(_hours()),
            profiles=InMemoryProfileRepository(
                {ALICE: "customer", BOB: "customer", ADMIN: "admin"}
            ),
            zone=KARACHI,
            slot_interval=timedelta(minutes=15),
            clock=lambda: self.now,
        )

    def book(self, user: UUID, start: datetime, svc: Service | None = None) -> Booking:
        return self.service.create_booking(user, (svc or self.haircut).id, start)


@pytest.fixture
def h() -> Harness:
    return Harness()


# ------------------------------------------------------------------ create_booking


def test_create_books_a_confirmed_slot_with_end_from_the_service_duration(h: Harness) -> None:
    booking = h.book(ALICE, monday(5, 0), h.combo)

    assert booking.status == "confirmed"
    assert booking.user_id == ALICE
    assert booking.service_id == h.combo.id
    assert (booking.start_at, booking.end_at) == (monday(5, 0), monday(5, 45))


def test_create_converts_any_timezone_offset_to_utc(h: Harness) -> None:
    local = datetime(2026, 10, 5, 10, 0, tzinfo=KARACHI)  # 05:00Z

    booking = h.book(ALICE, local)

    assert booking.start_at == monday(5, 0)
    assert booking.start_at.utcoffset() == timedelta(0)


def test_create_unknown_service_is_not_found(h: Harness) -> None:
    with pytest.raises(ServiceNotFoundError):
        h.service.create_booking(ALICE, uuid.uuid4(), monday(5))


def test_create_in_the_past_is_rejected(h: Harness) -> None:
    with pytest.raises(BookingInPastError):
        h.book(ALICE, datetime(2026, 9, 30, 5, 0, tzinfo=UTC))


def test_create_starting_exactly_now_is_rejected(h: Harness) -> None:
    h.now = monday(5, 0)

    with pytest.raises(BookingInPastError):
        h.book(ALICE, monday(5, 0))


def test_create_before_opening_is_rejected(h: Harness) -> None:
    with pytest.raises(OutsideBusinessHoursError):
        h.book(ALICE, monday(3, 45))  # 08:45 local


def test_create_running_past_closing_is_rejected_but_ending_exactly_at_close_is_fine(
    h: Harness,
) -> None:
    with pytest.raises(OutsideBusinessHoursError):
        h.book(ALICE, monday(12, 45))  # ends 13:15Z, after 13:00Z close

    assert h.book(ALICE, monday(12, 30)).end_at == monday(13, 0)


def test_create_on_a_closed_day_is_rejected(h: Harness) -> None:
    with pytest.raises(OutsideBusinessHoursError):
        h.book(ALICE, datetime(2026, 10, 4, 6, 0, tzinfo=UTC))


def test_create_off_the_slot_grid_is_rejected(h: Harness) -> None:
    with pytest.raises(InvalidSlotTimeError):
        h.book(ALICE, monday(5, 7))


def test_create_overlapping_another_booking_is_rejected(h: Harness) -> None:
    h.book(ALICE, monday(5, 0))

    with pytest.raises(SlotUnavailableError):
        h.book(BOB, monday(5, 15))


def test_create_back_to_back_is_allowed(h: Harness) -> None:
    h.book(ALICE, monday(5, 0))

    assert h.book(BOB, monday(5, 30)).user_id == BOB


def test_a_database_overlap_rejection_becomes_slot_unavailable(h: Harness) -> None:
    # Two requests can both pass validation; the DB constraint decides who wins.
    def lose_the_race(*_: object) -> Booking:
        raise SlotTakenError

    h.bookings.create = lose_the_race  # type: ignore[method-assign]

    with pytest.raises(SlotUnavailableError):
        h.book(ALICE, monday(5, 0))


def test_failed_validation_creates_nothing(h: Harness) -> None:
    with pytest.raises(InvalidSlotTimeError):
        h.book(ALICE, monday(5, 7))

    assert h.bookings.rows == {}


# -------------------------------------------------------------- reschedule_booking


def test_reschedule_moves_the_same_booking_in_place(h: Harness) -> None:
    booking = h.book(ALICE, monday(5, 0))

    moved = h.service.reschedule_booking(ALICE, booking.id, monday(7, 0))

    assert moved.id == booking.id
    assert (moved.start_at, moved.end_at) == (monday(7, 0), monday(7, 30))
    assert len(h.bookings.rows) == 1


def test_reschedule_keeps_the_service_duration(h: Harness) -> None:
    booking = h.book(ALICE, monday(5, 0), h.combo)

    moved = h.service.reschedule_booking(ALICE, booking.id, monday(7, 0))

    assert moved.end_at - moved.start_at == timedelta(minutes=45)


def test_reschedule_onto_its_own_overlapping_time_succeeds(h: Harness) -> None:
    booking = h.book(ALICE, monday(5, 0))

    moved = h.service.reschedule_booking(ALICE, booking.id, monday(5, 15))

    assert moved.start_at == monday(5, 15)


def test_reschedule_onto_another_bookings_time_fails_and_leaves_the_booking_unchanged(
    h: Harness,
) -> None:
    h.book(BOB, monday(5, 0))
    mine = h.book(ALICE, monday(7, 0))

    with pytest.raises(SlotUnavailableError):
        h.service.reschedule_booking(ALICE, mine.id, monday(5, 15))

    assert h.bookings.rows[mine.id].start_at == monday(7, 0)


def test_reschedule_someone_elses_booking_is_not_found(h: Harness) -> None:
    booking = h.book(ALICE, monday(5, 0))

    with pytest.raises(BookingNotFoundError):
        h.service.reschedule_booking(BOB, booking.id, monday(7, 0))


def test_reschedule_unknown_booking_is_not_found(h: Harness) -> None:
    with pytest.raises(BookingNotFoundError):
        h.service.reschedule_booking(ALICE, uuid.uuid4(), monday(7, 0))


def test_reschedule_a_cancelled_booking_is_not_active(h: Harness) -> None:
    booking = h.book(ALICE, monday(5, 0))
    h.service.cancel_booking(ALICE, booking.id)

    with pytest.raises(BookingNotActiveError):
        h.service.reschedule_booking(ALICE, booking.id, monday(7, 0))


def test_reschedule_to_the_past_is_rejected(h: Harness) -> None:
    booking = h.book(ALICE, monday(5, 0))

    with pytest.raises(BookingInPastError):
        h.service.reschedule_booking(ALICE, booking.id, datetime(2026, 9, 30, 5, 0, tzinfo=UTC))


def test_reschedule_outside_hours_or_off_grid_is_rejected(h: Harness) -> None:
    booking = h.book(ALICE, monday(5, 0))

    with pytest.raises(OutsideBusinessHoursError):
        h.service.reschedule_booking(ALICE, booking.id, monday(3, 0))
    with pytest.raises(InvalidSlotTimeError):
        h.service.reschedule_booking(ALICE, booking.id, monday(5, 7))


def test_a_booking_that_already_started_cannot_be_rescheduled(h: Harness) -> None:
    booking = h.book(ALICE, monday(5, 0))
    h.now = monday(5, 10)

    with pytest.raises(BookingInPastError):
        h.service.reschedule_booking(ALICE, booking.id, monday(7, 0))


def test_reschedule_that_loses_a_race_with_a_cancellation_is_not_active(h: Harness) -> None:
    booking = h.book(ALICE, monday(5, 0))
    h.bookings.update_times = lambda *_: None  # type: ignore[method-assign]

    with pytest.raises(BookingNotActiveError):
        h.service.reschedule_booking(ALICE, booking.id, monday(7, 0))


def test_reschedule_database_overlap_rejection_becomes_slot_unavailable(h: Harness) -> None:
    booking = h.book(ALICE, monday(5, 0))

    def lose_the_race(*_: object) -> Booking:
        raise SlotTakenError

    h.bookings.update_times = lose_the_race  # type: ignore[method-assign]

    with pytest.raises(SlotUnavailableError):
        h.service.reschedule_booking(ALICE, booking.id, monday(7, 0))


# ----------------------------------------------------------------- cancel_booking


def test_cancel_marks_the_booking_cancelled_and_keeps_the_row(h: Harness) -> None:
    booking = h.book(ALICE, monday(5, 0))

    cancelled = h.service.cancel_booking(ALICE, booking.id)

    assert cancelled.status == "cancelled"
    assert [b.status for b in h.service.list_my_bookings(ALICE)] == ["cancelled"]


def test_cancel_frees_the_slot_for_someone_else(h: Harness) -> None:
    booking = h.book(ALICE, monday(5, 0))
    h.service.cancel_booking(ALICE, booking.id)

    assert h.book(BOB, monday(5, 0)).user_id == BOB


def test_cancel_someone_elses_booking_is_not_found(h: Harness) -> None:
    booking = h.book(ALICE, monday(5, 0))

    with pytest.raises(BookingNotFoundError):
        h.service.cancel_booking(BOB, booking.id)
    assert h.bookings.rows[booking.id].status == "confirmed"


def test_cancel_unknown_booking_is_not_found(h: Harness) -> None:
    with pytest.raises(BookingNotFoundError):
        h.service.cancel_booking(ALICE, uuid.uuid4())


def test_cancelling_twice_is_not_active(h: Harness) -> None:
    booking = h.book(ALICE, monday(5, 0))
    h.service.cancel_booking(ALICE, booking.id)

    with pytest.raises(BookingNotActiveError):
        h.service.cancel_booking(ALICE, booking.id)


def test_a_booking_that_already_started_cannot_be_cancelled(h: Harness) -> None:
    booking = h.book(ALICE, monday(5, 0))
    h.now = monday(5, 10)

    with pytest.raises(BookingInPastError):
        h.service.cancel_booking(ALICE, booking.id)


def test_cancel_that_loses_a_race_with_another_cancellation_is_not_active(h: Harness) -> None:
    booking = h.book(ALICE, monday(5, 0))
    h.bookings.cancel = lambda *_: None  # type: ignore[method-assign]

    with pytest.raises(BookingNotActiveError):
        h.service.cancel_booking(ALICE, booking.id)


# ------------------------------------------------------------------- listings


def test_list_my_bookings_returns_only_my_bookings_newest_first(h: Harness) -> None:
    early = h.book(ALICE, monday(5, 0))
    late = h.book(ALICE, monday(7, 0))
    h.book(BOB, monday(9, 0))

    assert [b.id for b in h.service.list_my_bookings(ALICE)] == [late.id, early.id]


def test_admin_can_list_everyones_bookings(h: Harness) -> None:
    h.book(ALICE, monday(5, 0))
    h.book(BOB, monday(7, 0))

    assert len(h.service.admin_list_bookings(ADMIN, limit=50, offset=0)) == 2


@pytest.mark.parametrize("actor", [ALICE, uuid.uuid4()])
def test_non_admins_and_unknown_users_cannot_list_everyones_bookings(
    h: Harness, actor: UUID
) -> None:
    h.book(ALICE, monday(5, 0))

    with pytest.raises(ForbiddenError):
        h.service.admin_list_bookings(actor, limit=50, offset=0)


def test_services_and_business_hours_are_listed(h: Harness) -> None:
    assert [s.name for s in h.service.list_services()] == ["Haircut", "Haircut & Beard"]
    assert [bh.day_of_week for bh in h.service.list_business_hours()] == [1, 2, 3, 4, 5, 6, 7]


# ---------------------------------------------------------------- get_availability


def test_availability_returns_slot_ranges_sized_to_the_service(h: Harness) -> None:
    slots = h.service.get_availability(h.combo.id, MONDAY)

    assert slots[0] == TimeRange(monday(4, 0), monday(4, 45))
    assert slots[-1] == TimeRange(monday(12, 15), monday(13, 0))


def test_availability_excludes_booked_times(h: Harness) -> None:
    h.book(ALICE, monday(5, 0))

    starts = [s.start for s in h.service.get_availability(h.haircut.id, MONDAY)]

    assert monday(5, 0) not in starts
    assert monday(4, 45) not in starts
    assert monday(4, 30) in starts
    assert monday(5, 30) in starts


def test_availability_excludes_the_past_part_of_today(h: Harness) -> None:
    h.now = monday(6, 7)

    starts = [s.start for s in h.service.get_availability(h.haircut.id, MONDAY)]

    assert starts[0] == monday(6, 15)


def test_availability_is_empty_on_a_closed_day(h: Harness) -> None:
    assert h.service.get_availability(h.haircut.id, SUNDAY) == []


def test_availability_for_an_unknown_service_is_not_found(h: Harness) -> None:
    with pytest.raises(ServiceNotFoundError):
        h.service.get_availability(uuid.uuid4(), MONDAY)


def test_cancelled_bookings_do_not_block_availability(h: Harness) -> None:
    booking = h.book(ALICE, monday(5, 0))
    h.service.cancel_booking(ALICE, booking.id)

    starts = [s.start for s in h.service.get_availability(h.haircut.id, MONDAY)]

    assert monday(5, 0) in starts
