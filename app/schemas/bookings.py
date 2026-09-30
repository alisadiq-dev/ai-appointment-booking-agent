from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, field_validator

BookingStatus = Literal["confirmed", "cancelled"]


class Booking(BaseModel):
    """A bookings row. Times are UTC."""

    id: UUID
    user_id: UUID
    service_id: UUID
    start_at: datetime
    end_at: datetime
    status: BookingStatus
    google_event_id: str | None

    @field_validator("start_at", "end_at")
    @classmethod
    def _as_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)
