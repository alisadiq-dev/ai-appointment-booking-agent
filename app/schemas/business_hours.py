from datetime import time

from pydantic import BaseModel, Field


class BusinessHour(BaseModel):
    """Opening hours for one ISO weekday (1 = Monday ... 7 = Sunday), as local wall-clock times.

    `open_time` and `close_time` are both None when the business is closed that day.
    """

    day_of_week: int = Field(ge=1, le=7)
    open_time: time | None
    close_time: time | None
