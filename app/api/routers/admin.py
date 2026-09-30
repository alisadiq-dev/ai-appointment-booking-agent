from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import BookingServiceDep, CurrentUser
from app.schemas.api import AdminBookingOut

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/bookings")
def list_all_bookings(
    user: CurrentUser,
    service: BookingServiceDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AdminBookingOut]:
    bookings = service.admin_list_bookings(user.id, limit=limit, offset=offset)
    return [AdminBookingOut.model_validate(b, from_attributes=True) for b in bookings]
