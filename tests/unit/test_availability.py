from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

import pytest

from app.schemas.business_hours import BusinessHour
from app.services.availability import (
    TimeRange,
    business_window,
    generate_slots,
    is_aligned,
    window_for_instant,
)

KARACHI = ZoneInfo("Asia/Karachi")  # UTC+5, no DST
LONDON = ZoneInfo("Europe/London")  # GMT/BST, DST
INTERVAL = timedelta(minutes=15)
LONG_AGO = datetime(2020, 1, 1, tzinfo=UTC)


def _hours() -> list[BusinessHour]:
    """Mon-Fri 09:00-18:00, Sat 10:00-16:00, Sun closed (ISO weekday numbers)."""
    rows = [
        BusinessHour(day_of_week=d, open_time=time(9), close_time=time(18)) for d in range(1, 6)
    ]
    rows.append(BusinessHour(day_of_week=6, open_time=time(10), close_time=time(16)))
    rows.append(BusinessHour(day_of_week=7, open_time=None, close_time=None))
    return rows


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


MONDAY = date(2026, 10, 5)
SUNDAY = date(2026, 10, 4)


# ---------------------------------------------------------------- business_window


def test_window_converts_local_business_hours_to_utc() -> None:
    window = business_window(MONDAY, _hours(), KARACHI)

    assert window == TimeRange(_utc(2026, 10, 5, 4, 0), _utc(2026, 10, 5, 13, 0))


def test_window_is_none_on_a_closed_day() -> None:
    assert business_window(SUNDAY, _hours(), KARACHI) is None


def test_window_is_none_when_the_weekday_has_no_row() -> None:
    hours = [h for h in _hours() if h.day_of_week != 1]

    assert business_window(MONDAY, hours, KARACHI) is None


def test_window_uses_the_saturday_hours() -> None:
    window = business_window(date(2026, 10, 10), _hours(), KARACHI)

    assert window == TimeRange(_utc(2026, 10, 10, 5, 0), _utc(2026, 10, 10, 11, 0))


@pytest.mark.parametrize(
    ("day", "expected_start_utc"),
    [
        (date(2026, 12, 7), _utc(2026, 12, 7, 9, 0)),  # winter: GMT, UTC+0
        (date(2026, 10, 5), _utc(2026, 10, 5, 8, 0)),  # summer: BST, UTC+1
    ],
)
def test_window_follows_dst_offset_changes(day: date, expected_start_utc: datetime) -> None:
    window = business_window(day, _hours(), LONDON)

    assert window is not None
    assert window.start == expected_start_utc
    assert window.end - window.start == timedelta(hours=9)


def test_spring_forward_day_is_one_hour_shorter_in_real_time() -> None:
    # London 2026-03-29: clocks jump 01:00 -> 02:00, so 00:00-03:00 local is only 2 real hours.
    hours = [BusinessHour(day_of_week=7, open_time=time(0), close_time=time(3))]

    window = business_window(date(2026, 3, 29), hours, LONDON)

    assert window == TimeRange(_utc(2026, 3, 29, 0, 0), _utc(2026, 3, 29, 2, 0))


def test_autumn_back_day_is_one_hour_longer_in_real_time() -> None:
    # London 2026-10-25: clocks go 02:00 -> 01:00, so 00:00-03:00 local is 4 real hours.
    hours = [BusinessHour(day_of_week=7, open_time=time(0), close_time=time(3))]

    window = business_window(date(2026, 10, 25), hours, LONDON)

    assert window == TimeRange(_utc(2026, 10, 24, 23, 0), _utc(2026, 10, 25, 3, 0))


def test_opening_time_that_does_not_exist_locally_does_not_raise() -> None:
    # 01:30 does not exist in London on 2026-03-29. It resolves to 01:30Z (= 02:30 BST).
    hours = [BusinessHour(day_of_week=7, open_time=time(1, 30), close_time=time(4))]

    window = business_window(date(2026, 3, 29), hours, LONDON)

    assert window is not None
    assert window.start == _utc(2026, 3, 29, 1, 30)


# ------------------------------------------------------------ window_for_instant


def test_window_for_instant_uses_the_local_date_not_the_utc_date() -> None:
    # 2026-10-04 23:30Z is 04:30 on Monday 5 Oct in Karachi.
    window = window_for_instant(_utc(2026, 10, 4, 23, 30), _hours(), KARACHI)

    assert window == TimeRange(_utc(2026, 10, 5, 4, 0), _utc(2026, 10, 5, 13, 0))


def test_window_for_instant_handles_the_local_date_across_a_dst_change() -> None:
    hours = [BusinessHour(day_of_week=7, open_time=time(0), close_time=time(3))]

    # 2026-10-24 23:30Z is 00:30 BST on Sunday 25 Oct in London.
    window = window_for_instant(_utc(2026, 10, 24, 23, 30), hours, LONDON)

    assert window == TimeRange(_utc(2026, 10, 24, 23, 0), _utc(2026, 10, 25, 3, 0))


def test_window_for_instant_is_none_on_a_closed_day() -> None:
    assert window_for_instant(_utc(2026, 10, 4, 6, 0), _hours(), KARACHI) is None


# -------------------------------------------------------------------- is_aligned


def test_slot_start_must_sit_on_the_grid_relative_to_opening() -> None:
    window = TimeRange(_utc(2026, 10, 5, 4, 0), _utc(2026, 10, 5, 13, 0))

    assert is_aligned(_utc(2026, 10, 5, 4, 15), window, INTERVAL)
    assert is_aligned(_utc(2026, 10, 5, 12, 30), window, INTERVAL)
    assert not is_aligned(_utc(2026, 10, 5, 4, 7), window, INTERVAL)
    assert not is_aligned(datetime(2026, 10, 5, 4, 15, 30, tzinfo=UTC), window, INTERVAL)


def test_grid_is_relative_to_opening_time_not_to_midnight() -> None:
    hours = [BusinessHour(day_of_week=1, open_time=time(9, 10), close_time=time(11))]
    window = business_window(MONDAY, hours, KARACHI)
    assert window is not None

    assert is_aligned(window.start + INTERVAL, window, INTERVAL)  # 09:25 local
    assert not is_aligned(_utc(2026, 10, 5, 4, 15), window, INTERVAL)  # 09:15 local


# ---------------------------------------------------------------- generate_slots


def _slots(**overrides: object) -> list[datetime]:
    args: dict[str, object] = {
        "day": MONDAY,
        "duration": timedelta(minutes=30),
        "hours": _hours(),
        "zone": KARACHI,
        "busy": [],
        "now": LONG_AGO,
        "interval": INTERVAL,
    }
    args.update(overrides)
    return generate_slots(**args)  # type: ignore[arg-type]


def test_slots_cover_the_whole_day_on_the_grid() -> None:
    slots = _slots()

    assert slots[0] == _utc(2026, 10, 5, 4, 0)
    assert slots[-1] == _utc(2026, 10, 5, 12, 30)  # last 30-min slot ends exactly at close
    assert len(slots) == 35
    assert all(b - a == INTERVAL for a, b in pairwise(slots))


def test_a_longer_service_has_an_earlier_last_start() -> None:
    slots = _slots(duration=timedelta(minutes=45))

    assert slots[-1] == _utc(2026, 10, 5, 12, 15)


def test_busy_interval_removes_every_overlapping_start_but_allows_back_to_back() -> None:
    busy = [TimeRange(_utc(2026, 10, 5, 5, 0), _utc(2026, 10, 5, 5, 30))]

    slots = _slots(busy=busy)

    assert _utc(2026, 10, 5, 4, 30) in slots  # ends exactly when the busy block starts
    for start in [(4, 45), (5, 0), (5, 15)]:
        assert _utc(2026, 10, 5, *start) not in slots
    assert _utc(2026, 10, 5, 5, 30) in slots  # starts exactly when the busy block ends


def test_slots_in_the_past_are_excluded() -> None:
    slots = _slots(now=_utc(2026, 10, 5, 6, 7))

    assert slots[0] == _utc(2026, 10, 5, 6, 15)


def test_a_slot_starting_exactly_now_is_excluded() -> None:
    slots = _slots(now=_utc(2026, 10, 5, 6, 15))

    assert slots[0] == _utc(2026, 10, 5, 6, 30)


def test_no_slots_when_the_whole_day_is_in_the_past() -> None:
    assert _slots(now=_utc(2026, 10, 6, 0, 0)) == []


def test_no_slots_on_a_closed_day() -> None:
    assert _slots(day=SUNDAY) == []


def test_no_slots_when_the_service_is_longer_than_the_opening_window() -> None:
    assert _slots(duration=timedelta(hours=10)) == []


def test_slot_grid_starts_at_a_non_round_opening_time() -> None:
    hours = [BusinessHour(day_of_week=1, open_time=time(9, 10), close_time=time(10, 0))]

    slots = _slots(hours=hours)

    # 09:10-10:00 local = 04:10Z-05:00Z; a 30-min service fits starting at 04:10 and 04:25 only
    assert slots == [_utc(2026, 10, 5, 4, 10), _utc(2026, 10, 5, 4, 25)]


def test_slots_on_the_spring_forward_day_reflect_real_elapsed_time() -> None:
    hours = [BusinessHour(day_of_week=7, open_time=time(0), close_time=time(3))]

    slots = _slots(day=date(2026, 3, 29), zone=LONDON, hours=hours)

    # 2 real hours (00:00Z-02:00Z), 30-min service, 15-min grid -> 7 starts
    assert len(slots) == 7
    assert slots[0] == _utc(2026, 3, 29, 0, 0)
    assert slots[-1] == _utc(2026, 3, 29, 1, 30)


def test_slots_on_the_autumn_back_day_reflect_real_elapsed_time() -> None:
    hours = [BusinessHour(day_of_week=7, open_time=time(0), close_time=time(3))]

    slots = _slots(day=date(2026, 10, 25), zone=LONDON, hours=hours)

    # 4 real hours (23:00Z-03:00Z) -> 15 starts
    assert len(slots) == 15
    assert slots[0] == _utc(2026, 10, 24, 23, 0)
    assert slots[-1] == _utc(2026, 10, 25, 2, 30)


def test_slots_are_always_utc_aware_datetimes() -> None:
    assert all(s.tzinfo is not None and s.utcoffset() == timedelta(0) for s in _slots())
