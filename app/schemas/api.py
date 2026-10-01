"""Request and response models for the HTTP API."""

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, StringConstraints

from app.schemas.bookings import BookingStatus
from app.schemas.business_hours import BusinessHour


class CreateBookingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_id: UUID
    start_at: AwareDatetime  # must carry a UTC offset; stored and returned in UTC


class RescheduleBookingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_at: AwareDatetime


class BookingOut(BaseModel):
    id: UUID
    service_id: UUID
    start_at: datetime
    end_at: datetime
    status: BookingStatus


class AdminBookingOut(BookingOut):
    user_id: UUID


class SlotOut(BaseModel):
    start_at: datetime
    end_at: datetime


class AvailabilityOut(BaseModel):
    service_id: UUID
    date: date
    timezone: str
    slots: list[SlotOut]


class BusinessHoursOut(BaseModel):
    timezone: str
    hours: list[BusinessHour]


MAX_CHAT_MESSAGE_CHARS = 1000


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Trimmed first, so whitespace alone is empty and padding cannot be used to dodge the limit.
    message: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_CHAT_MESSAGE_CHARS),
    ]


class ChatResponse(BaseModel):
    reply: str
