"""Business outcomes at execute time (friendly reply, stale proposal cleared, fresh slots offered)
versus infrastructure failures (always propagate so the request rolls back)."""

import psycopg
import pytest

from app.agents.model import Interpretation
from app.services.errors import CalendarUnavailableError
from tests.agent_helpers import ALICE, BOB, AgentHarness, monday_local

BOOK = Interpretation(action="book", service_name="Haircut", date="2026-10-05", time="10:00")
RESCHEDULE = Interpretation(action="reschedule", date="2026-10-06", time="11:00")


def test_slot_taken_between_proposal_and_yes_offers_fresh_slots() -> None:
    h = AgentHarness(BOOK, Interpretation(action="other", time="10:30"))
    h.say("book a haircut monday 10am")
    h.book_directly(BOB, monday_local(10))

    reply = h.say("yes")

    assert "no longer available" in reply
    assert "Free times that day" in reply
    assert "10:30" in reply
    assert "09:00" in reply
    assert "10:00," not in reply  # the taken slot is not offered again
    assert [b.user_id for b in h.written().values()] == [BOB]
    state = h.sessions.load(ALICE)
    assert state["pending"] is None  # the stale proposal is gone
    assert "time" not in state["draft"]
    h.say("how about 10:30?")  # the conversation continues from the same day and service
    h.say("yes")
    assert sorted(b.start_at for b in h.written().values()) == [
        monday_local(10),
        monday_local(10, 30),
    ]


def test_yes_cannot_replay_a_proposal_whose_slot_was_taken() -> None:
    h = AgentHarness(BOOK)
    h.say("book a haircut monday 10am")
    h.book_directly(BOB, monday_local(10))
    h.say("yes")
    h.bookings.rows.pop(next(iter(h.bookings.rows)))  # Bob's booking disappears again

    h.say("yes")

    assert h.written() == {}  # the old proposal was cleared, a later yes confirms nothing


def test_reschedule_target_slot_taken_leaves_the_booking_and_offers_fresh_slots() -> None:
    h = AgentHarness(RESCHEDULE)
    mine = h.book_directly(ALICE, monday_local(10))
    h.say("move my booking to tuesday 11am")
    h.book_directly(BOB, monday_local(11, day=6))

    reply = h.say("yes")

    assert h.bookings.rows[mine.id].start_at == monday_local(10)
    assert "no longer available" in reply
    assert "Free times that day" in reply
    assert h.sessions.load(ALICE)["pending"] is None


def test_booking_cancelled_meanwhile_cannot_be_rescheduled_and_fresh_slots_are_offered() -> None:
    h = AgentHarness(RESCHEDULE)
    mine = h.book_directly(ALICE, monday_local(10))
    h.say("move my booking to tuesday 11am")
    h.booking_service.cancel_booking(ALICE, mine.id)  # e.g. cancelled from another device

    reply = h.say("yes")

    assert h.bookings.rows[mine.id].status == "cancelled"
    assert h.bookings.rows[mine.id].start_at == monday_local(10)  # not moved
    assert "no longer active" in reply
    assert "Free times that day" in reply
    assert "09:00" in reply
    state = h.sessions.load(ALICE)
    assert state["pending"] is None
    assert "booking_id" not in state["draft"]


def test_booking_cancelled_meanwhile_makes_a_cancel_yes_a_harmless_reply() -> None:
    h = AgentHarness(Interpretation(action="cancel"))
    mine = h.book_directly(ALICE, monday_local(10))
    h.say("cancel my booking")
    h.booking_service.cancel_booking(ALICE, mine.id)
    deletes = h.calendar.calls.count("delete_event")

    reply = h.say("yes")

    assert "no longer active" in reply
    assert "Nothing has been changed" in reply
    assert h.calendar.calls.count("delete_event") == deletes  # no second calendar call
    assert ALICE not in h.sessions.rows  # nothing stale left behind
    h.say("yes")  # and a repeated yes confirms nothing
    assert h.calendar.calls.count("delete_event") == deletes


# ------------------------------------------------------------------ infrastructure still propagates


def test_database_failure_during_execute_propagates_and_keeps_the_proposal() -> None:
    h = AgentHarness(BOOK)
    h.say("book a haircut monday 10am")

    def broken(*args: object, **kwargs: object) -> None:
        raise psycopg.OperationalError("connection lost")

    h.booking_service.create_booking = broken  # type: ignore[method-assign]

    with pytest.raises(psycopg.OperationalError):
        h.say("yes")

    assert h.sessions.load(ALICE)["pending"] is not None  # state not saved or cleared
    assert h.written() == {}


@pytest.mark.parametrize(
    ("script", "operation", "setup"),
    [
        (RESCHEDULE, "update_event", "move my booking to tuesday 11am"),
        (Interpretation(action="cancel"), "delete_event", "cancel my booking"),
    ],
)
def test_calendar_failure_on_reschedule_or_cancel_propagates_and_rolls_back(
    script: Interpretation, operation: str, setup: str
) -> None:
    h = AgentHarness(script)
    mine = h.book_directly(ALICE, monday_local(10))
    h.say(setup)
    h.calendar.failing = {operation}
    snapshot = h.bookings.snapshot()

    with pytest.raises(CalendarUnavailableError):
        h.say("yes")
    h.bookings.restore(snapshot)  # what the request transaction does on an exception

    assert h.bookings.rows[mine.id].status == "confirmed"
    assert h.bookings.rows[mine.id].start_at == monday_local(10)
    assert h.sessions.load(ALICE)["pending"] is not None


def test_calendar_failure_while_listing_fresh_slots_propagates() -> None:
    h = AgentHarness(BOOK)
    h.say("book a haircut monday 10am")
    h.book_directly(BOB, monday_local(10))
    h.calendar.failing = {"list_busy"}

    with pytest.raises(CalendarUnavailableError):
        h.say("yes")
