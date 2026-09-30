from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path

from app.api.deps import BookingServiceDep, CurrentUser
from app.schemas.api import BookingOut, CreateBookingRequest, RescheduleBookingRequest

router = APIRouter(prefix="/bookings", tags=["bookings"])

BookingId = Annotated[UUID, Path()]


@router.post("", status_code=201)
def create_booking(
    body: CreateBookingRequest, user: CurrentUser, service: BookingServiceDep
) -> BookingOut:
    booking = service.create_booking(user.id, body.service_id, body.start_at)
    return BookingOut.model_validate(booking, from_attributes=True)


@router.get("")
def list_my_bookings(user: CurrentUser, service: BookingServiceDep) -> list[BookingOut]:
    return [
        BookingOut.model_validate(b, from_attributes=True)
        for b in service.list_my_bookings(user.id)
    ]


@router.patch("/{booking_id}")
def reschedule_booking(
    booking_id: BookingId,
    body: RescheduleBookingRequest,
    user: CurrentUser,
    service: BookingServiceDep,
) -> BookingOut:
    booking = service.reschedule_booking(user.id, booking_id, body.start_at)
    return BookingOut.model_validate(booking, from_attributes=True)


@router.post("/{booking_id}/cancel")
def cancel_booking(
    booking_id: BookingId, user: CurrentUser, service: BookingServiceDep
) -> BookingOut:
    booking = service.cancel_booking(user.id, booking_id)
    return BookingOut.model_validate(booking, from_attributes=True)
