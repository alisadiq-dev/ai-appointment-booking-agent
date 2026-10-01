"""A finished booking, reschedule or cancellation resets the user's session (next chat is fresh)."""

from app.agents.model import Interpretation
from tests.agent_helpers import ALICE, BOB, AgentHarness, monday_local

BOOK = Interpretation(action="book", service_name="Haircut", date="2026-10-05", time="10:00")


def test_session_state_exists_while_a_conversation_is_in_progress() -> None:
    h = AgentHarness(Interpretation(action="book", service_name="Haircut"))

    h.say("I'd like a haircut")

    state = h.sessions.load(ALICE)
    assert state["draft"]["action"] == "book"


def test_booking_completed_resets_the_session() -> None:
    h = AgentHarness(BOOK)
    h.say("book a haircut monday 10am")
    assert h.sessions.load(ALICE)["pending"] is not None

    h.say("yes")

    assert ALICE not in h.sessions.rows


def test_reschedule_completed_resets_the_session() -> None:
    h = AgentHarness(Interpretation(action="reschedule", date="2026-10-06", time="11:00"))
    h.book_directly(ALICE, monday_local(10))
    h.say("move my booking to tuesday 11am")

    h.say("yes")

    assert ALICE not in h.sessions.rows


def test_cancellation_completed_resets_the_session() -> None:
    h = AgentHarness(Interpretation(action="cancel"))
    h.book_directly(ALICE, monday_local(10))
    h.say("cancel my booking")

    h.say("yes")

    assert ALICE not in h.sessions.rows


def test_the_next_chat_after_a_booking_starts_fresh() -> None:
    h = AgentHarness(BOOK, Interpretation(action="book"))
    h.say("book a haircut monday 10am")
    h.say("yes")

    reply = h.say("I want to book again")

    assert "Which service" in reply  # nothing carried over from the previous booking


def test_resetting_one_user_does_not_touch_another_users_session() -> None:
    h = AgentHarness(Interpretation(action="book", service_name="Haircut"), BOOK)
    h.say("a haircut please", user=BOB)
    h.say("book a haircut monday 10am")
    h.say("yes")

    assert ALICE not in h.sessions.rows
    assert h.sessions.load(BOB)["draft"]["action"] == "book"
