from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class CalendarEvent:
    """What the booking system asks the calendar to hold for one booking."""

    booking_id: UUID
    summary: str
    description: str
    start: datetime
    end: datetime
