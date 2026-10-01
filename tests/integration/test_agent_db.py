"""The agent on real Postgres: real repositories, real session table, scripted model."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.agents.graph import BookingAgent
from app.agents.model import Interpretation
from app.repositories.bookings import BookingRepository
from app.repositories.business_hours import BusinessHoursRepository
from app.repositories.profiles import ProfileRepository
from app.repositories.services import ServiceRepository
from app.repositories.sessions import SessionRepository
from app.services.booking_service import BookingService
from tests.agent_helpers import ScriptedModel
from tests.fakes import FakeCalendar
from tests.integration.conftest import Conn, make_user, requires_db

pytestmark = [pytest.mark.local_supabase, requires_db]

NOW = datetime(2030, 1, 1, tzinfo=UTC)
KARACHI = ZoneInfo("Asia/Karachi")
BOOK = Interpretation(action="book", service_name="Haircut", date="2030-01-07", time="10:00")


def build_agent(conn: Conn, model: ScriptedModel) -> BookingAgent:
    service = BookingService(
        bookings=BookingRepository(conn),
        services=ServiceRepository(conn),
        business_hours=BusinessHoursRepository(conn),
        profiles=ProfileRepository(conn),
        calendar=FakeCalendar(),
        zone=KARACHI,
        slot_interval=timedelta(minutes=15),
        clock=lambda: NOW,
    )
    return BookingAgent(
        bookings=service,
        model=model,
        sessions=SessionRepository(conn),
        zone=KARACHI,
        clock=lambda: NOW,
    )


def test_book_then_cancel_with_confirmation_on_real_postgres(conn: Conn) -> None:
    user = make_user(conn)
    agent = build_agent(conn, ScriptedModel(BOOK, Interpretation(action="cancel")))
    repo, sessions = BookingRepository(conn), SessionRepository(conn)

    agent.handle(user, "book a haircut monday 10am")
    assert repo.list_for_user(user) == []  # proposed, not written
    assert sessions.load(user)["pending"] is not None  # state survives between turns in Postgres

    agent.handle(user, "yes")
    [booking] = repo.list_for_user(user)
    assert booking.status == "confirmed"
    assert sessions.load(user) == {}  # session reset after completion

    agent.handle(user, "cancel my booking")
    assert repo.get_for_user(booking.id, user).status == "confirmed"  # type: ignore[union-attr]
    agent.handle(user, "yes")
    assert repo.get_for_user(booking.id, user).status == "cancelled"  # type: ignore[union-attr]
    assert sessions.load(user) == {}


def test_a_user_cannot_cancel_another_users_booking_through_the_agent(conn: Conn) -> None:
    alice, bob = make_user(conn), make_user(conn)
    repo = BookingRepository(conn)
    bobs_agent = build_agent(conn, ScriptedModel(BOOK))
    bobs_agent.handle(bob, "book a haircut monday 10am")
    bobs_agent.handle(bob, "yes")
    [bobs_booking] = repo.list_for_user(bob)
    alices_agent = build_agent(conn, ScriptedModel(Interpretation(action="cancel")))

    reply = alices_agent.handle(alice, f"cancel booking {bobs_booking.id}")
    alices_agent.handle(alice, "yes")

    assert "no upcoming" in reply.lower()
    assert repo.get_for_user(bobs_booking.id, bob).status == "confirmed"  # type: ignore[union-attr]
