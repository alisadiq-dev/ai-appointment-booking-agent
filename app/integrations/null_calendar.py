from datetime import datetime

from app.schemas.calendar import CalendarEvent
from app.schemas.time_range import TimeRange


class NullCalendar:
    """Used when CALENDAR_ENABLED is false (local dev, tests): nothing is synced anywhere."""

    def create_event(self, event: CalendarEvent) -> str:
        return event.booking_id.hex

    def update_event(self, event_id: str, start: datetime, end: datetime) -> None:
        return None

    def delete_event(self, event_id: str) -> None:
        return None

    def list_busy(self, start: datetime, end: datetime) -> list[TimeRange]:
        return []
