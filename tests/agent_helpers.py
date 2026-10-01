"""Shared agent test fixtures: a real BookingService on in-memory fakes and a scripted model."""

import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from app.agents.graph import BookingAgent
from app.agents.model import Interpretation, ModelContext, ModelUnavailableError
from app.schemas.bookings import Booking
from app.schemas.business_hours import BusinessHour
from app.services.booking_service import BookingService
from tests.fakes import (
    FakeCalendar,
    InMemoryBookingRepository,
    InMemoryBusinessHoursRepository,
    InMemoryProfileRepository,
    InMemoryServiceRepository,
    InMemorySessionStore,
    make_service,
)

KARACHI = ZoneInfo("Asia/Karachi")  # UTC+5
NOW = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)  # Thursday
ALICE = uuid.uuid4()
BOB = uuid.uuid4()


def monday_local(hour: int, minute: int = 0, day: int = 5) -> datetime:
    """Local (Karachi) wall-clock time on Monday 5 Oct 2026 (open 09:00-18:00), as UTC."""
    return datetime(2026, 10, day, hour, minute, tzinfo=KARACHI).astimezone(UTC)


class ScriptedModel:
    """Returns queued interpretations (or raises queued errors) and records what it was shown."""

    def __init__(self, *script: Interpretation | Exception) -> None:
        self.script = list(script)
        self.calls: list[tuple[str, ModelContext]] = []

    def interpret(self, message: str, context: ModelContext) -> Interpretation:
        self.calls.append((message, context))
        if not self.script:
            raise ModelUnavailableError("script exhausted")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class AgentHarness:
    def __init__(self, *script: Interpretation | Exception) -> None:
        self.haircut = make_service("Haircut", 30)
        self.beard = make_service("Beard Trim", 15)
        self.bookings = InMemoryBookingRepository()
        self.calendar = FakeCalendar()
        self.sessions = InMemorySessionStore()
        self.model = ScriptedModel(*script)
        hours = [
            BusinessHour(day_of_week=d, open_time=_t(9), close_time=_t(18)) for d in range(1, 6)
        ]
        hours += [
            BusinessHour(day_of_week=6, open_time=_t(10), close_time=_t(16)),
            BusinessHour(day_of_week=7, open_time=None, close_time=None),
        ]
        self.booking_service = BookingService(
            bookings=self.bookings,
            services=InMemoryServiceRepository([self.haircut, self.beard]),
            business_hours=InMemoryBusinessHoursRepository(hours),
            profiles=InMemoryProfileRepository({ALICE: "customer", BOB: "customer"}),
            calendar=self.calendar,
            zone=KARACHI,
            slot_interval=timedelta(minutes=15),
            clock=lambda: self.now,
        )
        self.now = NOW
        self.agent = BookingAgent(
            bookings=self.booking_service,
            model=self.model,
            sessions=self.sessions,
            zone=KARACHI,
            clock=lambda: self.now,
        )

    def say(self, message: str, user: UUID = ALICE) -> str:
        return self.agent.handle(user, message)

    def book_directly(self, user: UUID, start: datetime, service: Any = None) -> Booking:
        return self.booking_service.create_booking(user, (service or self.haircut).id, start)

    def written(self) -> dict[UUID, Booking]:
        return dict(self.bookings.rows)


def _t(hour: int) -> time:

    return time(hour)
