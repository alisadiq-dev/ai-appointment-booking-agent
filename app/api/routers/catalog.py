from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import BookingServiceDep, CurrentUser
from app.core.config import Settings, get_settings
from app.schemas.api import AvailabilityOut, BusinessHoursOut, SlotOut
from app.schemas.services import Service

router = APIRouter(tags=["catalog"])


@router.get("/services")
def list_services(_: CurrentUser, service: BookingServiceDep) -> list[Service]:
    return service.list_services()


@router.get("/business-hours")
def list_business_hours(
    _: CurrentUser,
    service: BookingServiceDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> BusinessHoursOut:
    return BusinessHoursOut(
        timezone=settings.business_timezone, hours=service.list_business_hours()
    )


@router.get("/availability")
def get_availability(
    _: CurrentUser,
    service: BookingServiceDep,
    settings: Annotated[Settings, Depends(get_settings)],
    service_id: Annotated[UUID, Query()],
    day: Annotated[date, Query(alias="date")],
) -> AvailabilityOut:
    slots = service.get_availability(service_id, day)
    return AvailabilityOut(
        service_id=service_id,
        date=day,
        timezone=settings.business_timezone,
        slots=[SlotOut(start_at=s.start, end_at=s.end) for s in slots],
    )
