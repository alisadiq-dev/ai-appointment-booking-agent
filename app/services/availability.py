"""Pure availability logic. No I/O, no clock: everything is passed in, so it is easy to test.

Business hours are wall-clock times in the business timezone; every instant returned here is
UTC. Time arithmetic on slots is done in UTC (real elapsed time), so DST changes shorten or
lengthen a day correctly.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.schemas.business_hours import BusinessHour
from app.schemas.time_range import TimeRange


def business_window(day: date, hours: Sequence[BusinessHour], zone: ZoneInfo) -> TimeRange | None:
    """The UTC opening window for a local calendar day, or None if closed / no hours defined."""
    for row in hours:
        if row.day_of_week != day.isoweekday():
            continue
        if row.open_time is None or row.close_time is None:
            return None
        opens = datetime.combine(day, row.open_time, tzinfo=zone).astimezone(UTC)
        closes = datetime.combine(day, row.close_time, tzinfo=zone).astimezone(UTC)
        return TimeRange(opens, closes)
    return None


def window_for_instant(
    instant: datetime, hours: Sequence[BusinessHour], zone: ZoneInfo
) -> TimeRange | None:
    """The opening window of the local calendar day that `instant` falls on."""
    return business_window(instant.astimezone(zone).date(), hours, zone)


def is_aligned(start: datetime, window: TimeRange, interval: timedelta) -> bool:
    """True if `start` is a whole number of intervals after the window opens."""
    offset = start - window.start
    return offset >= timedelta(0) and offset % interval == timedelta(0)


def generate_slots(
    *,
    day: date,
    duration: timedelta,
    hours: Sequence[BusinessHour],
    zone: ZoneInfo,
    busy: Sequence[TimeRange],
    now: datetime,
    interval: timedelta,
) -> list[datetime]:
    """UTC start times of free slots for a service of `duration` on a local calendar day.

    A slot must end by closing time, start strictly after `now`, and not overlap a busy range
    (back-to-back is allowed). Starts are on a grid of `interval` from opening time.
    """
    window = business_window(day, hours, zone)
    if window is None:
        return []

    slots: list[datetime] = []
    start = window.start
    while start + duration <= window.end:
        end = start + duration
        if start > now and not any(b.overlaps(start, end) for b in busy):
            slots.append(start)
        start += interval
    return slots
