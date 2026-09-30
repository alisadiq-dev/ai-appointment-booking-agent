from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel


class Service(BaseModel):
    id: UUID
    name: str
    duration_minutes: int
    price: Decimal
