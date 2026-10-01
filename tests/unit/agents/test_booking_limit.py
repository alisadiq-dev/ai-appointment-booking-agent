"""The booking limit in chat: a friendly reply and no write, never a raw error."""

from app.agents.model import Interpretation
from tests.agent_helpers import ALICE, BOB, AgentHarness, monday_local

BOOK = Interpretation(action="book", service_name="Haircut", date="2026-10-05", time="13:00")


def _full(h: AgentHarness) -> None:
    for hour in (9, 10, 11):
        h.book_directly(ALICE, monday_local(hour))


def test_a_user_at_the_limit_asking_to_book_gets_a_friendly_reply_and_nothing_is_written() -> None:
    h = AgentHarness(BOOK)
    _full(h)
    before = h.written()

    reply = h.say("book a haircut monday 1pm")

    assert "3" in reply
    assert "cancel" in reply.lower() or "move" in reply.lower()
    assert "Nothing has been changed" in reply
    assert h.written() == before
    assert h.sessions.load(ALICE).get("pending") is None  # no proposal to confirm


def test_a_yes_after_that_cannot_book_anything() -> None:
    h = AgentHarness(BOOK)
    _full(h)
    h.say("book a haircut monday 1pm")

    h.say("yes")

    assert len(h.written()) == 3


def test_limit_reached_between_proposal_and_yes_is_a_friendly_reply_with_no_write() -> None:
    h = AgentHarness(BOOK)
    h.book_directly(ALICE, monday_local(9))
    h.book_directly(ALICE, monday_local(10))
    h.say("book a haircut monday 1pm")  # a place is left, so a proposal is made
    h.book_directly(ALICE, monday_local(11))  # e.g. booked from another device meanwhile

    reply = h.say("yes")

    assert "3" in reply
    assert "Nothing has been changed" in reply
    assert len(h.written()) == 3
    assert h.sessions.load(ALICE).get("pending") is None


def test_the_limit_counts_only_the_users_own_active_future_bookings() -> None:
    h = AgentHarness(BOOK)
    for hour in (9, 10, 11):
        h.book_directly(BOB, monday_local(hour))  # Bob is full, Alice has nothing

    reply = h.say("book a haircut monday 1pm")

    assert "Shall I book" in reply


def test_rescheduling_at_the_limit_is_still_allowed_in_chat() -> None:
    first = Interpretation(action="reschedule", booking_number=1, date="2026-10-06", time="11:00")
    h = AgentHarness(first)
    mine = h.book_directly(ALICE, monday_local(9))
    h.book_directly(ALICE, monday_local(10))
    h.book_directly(ALICE, monday_local(11))

    reply = h.say("move my first booking to tuesday 11am")
    assert "Shall I move" in reply
    h.say("yes")

    assert len(h.written()) == 3
    assert h.written()[mine.id].start_at == monday_local(11, day=6)
