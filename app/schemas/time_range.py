from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TimeRange:
    """A half-open UTC interval [start, end)."""

    start: datetime
    end: datetime

    def overlaps(self, start: datetime, end: datetime) -> bool:
        return self.start < end and start < self.end
