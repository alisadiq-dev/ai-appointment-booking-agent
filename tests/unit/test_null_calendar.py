import uuid
from datetime import UTC, datetime

from app.integrations.null_calendar import NullCalendar
from app.schemas.calendar import CalendarEvent

START = datetime(2031, 3, 4, 5, 0, tzinfo=UTC)


def test_the_no_op_calendar_syncs_nothing_but_honours_the_port_contract() -> None:
    calendar = NullCalendar()
    booking_id = uuid.uuid4()

    event_id = calendar.create_event(CalendarEvent(booking_id, "x", "y", START, START))

    assert event_id == booking_id.hex  # the port requires the event id to be the booking id
    assert calendar.update_event(event_id, START, START) is None
    assert calendar.delete_event(event_id) is None
    assert calendar.list_busy(START, START) == []  # nothing ever blocks time
