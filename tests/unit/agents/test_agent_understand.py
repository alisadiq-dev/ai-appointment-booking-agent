"""understand + plan + propose nodes: what the agent asks, shows and remembers (no writes yet)."""

from app.agents.model import Interpretation, ModelUnavailableError
from tests.agent_helpers import ALICE, BOB, AgentHarness, monday_local


def test_model_failure_gives_the_fallback_and_changes_nothing() -> None:
    h = AgentHarness(ModelUnavailableError("down"))

    reply = h.say("book a haircut monday 10am")

    assert "can't" in reply.lower() or "cannot" in reply.lower()
    assert h.written() == {}
    assert h.sessions.rows == {}


def test_no_model_configured_keeps_working_as_a_fallback() -> None:
    h = AgentHarness()  # empty script: every call raises ModelUnavailableError

    assert "try again" in h.say("hello").lower()
    assert h.written() == {}


def test_small_talk_gets_a_help_reply_listing_what_the_agent_can_do() -> None:
    h = AgentHarness(Interpretation(action="other"))

    reply = h.say("hello")

    assert "book" in reply.lower()
    assert h.written() == {}


def test_missing_details_are_asked_for_one_step_at_a_time() -> None:
    h = AgentHarness(Interpretation(action="book"))

    reply = h.say("I want to book")

    assert "Haircut" in reply
    assert "Beard Trim" in reply  # asks which service


def test_details_collected_over_several_turns_are_remembered() -> None:
    h = AgentHarness(
        Interpretation(action="book", service_name="haircut"),
        Interpretation(action="other", date="2026-10-05"),
        Interpretation(action="other", time="10:00"),
    )

    assert "date" in h.say("a haircut please").lower()
    assert "time" in h.say("monday").lower()
    reply = h.say("10am")

    assert "Haircut" in reply
    assert "Mon 05 Oct 2026 at 10:00" in reply
    assert "yes" in reply.lower()
    assert h.written() == {}  # a proposal is not a booking


def test_unknown_service_name_from_the_model_is_ignored() -> None:
    h = AgentHarness(Interpretation(action="book", service_name="Dragon Taming"))

    reply = h.say("book dragon taming")

    assert "Haircut" in reply  # asks again which service


def test_malformed_date_and_time_from_the_model_are_ignored() -> None:
    h = AgentHarness(
        Interpretation(action="book", service_name="Haircut", date="next-ish", time="25:99")
    )

    reply = h.say("book a haircut sometime")

    assert "date" in reply.lower()


def test_unavailable_time_lists_free_times_instead_of_proposing() -> None:
    h = AgentHarness(
        Interpretation(action="book", service_name="Haircut", date="2026-10-05", time="03:00")
    )

    reply = h.say("book haircut at 3am")

    assert "isn't available" in reply
    assert "09:00" in reply
    assert "yes" not in reply.lower()
    assert h.written() == {}


def test_a_time_already_booked_by_someone_else_is_not_proposed() -> None:
    h = AgentHarness(
        Interpretation(action="book", service_name="Haircut", date="2026-10-05", time="10:00")
    )
    h.book_directly(BOB, monday_local(10))

    reply = h.say("haircut monday 10am")

    assert "isn't available" in reply


def test_availability_question_lists_free_times_and_books_nothing() -> None:
    h = AgentHarness(
        Interpretation(action="availability", service_name="Haircut", date="2026-10-05")
    )

    reply = h.say("what's free monday for a haircut?")

    assert "09:00" in reply
    assert "10:45" in reply
    assert h.written() == {}


def test_the_model_only_sees_my_own_upcoming_bookings() -> None:
    h = AgentHarness(Interpretation(action="other"))
    mine = h.book_directly(ALICE, monday_local(10))
    h.book_directly(BOB, monday_local(12))
    cancelled = h.book_directly(ALICE, monday_local(14))
    h.booking_service.cancel_booking(ALICE, cancelled.id)

    h.say("hello")

    context = h.model.calls[0][1]
    assert [b.number for b in context.bookings] == [1]
    assert "10:00" in context.bookings[0].description
    assert str(mine.id) not in context.bookings[0].description
    assert context.service_names == ["Beard Trim", "Haircut"]
    assert context.today == "2026-10-01"


def test_user_text_is_passed_to_the_model_unchanged_and_length_limited() -> None:
    h = AgentHarness(Interpretation(action="other"))

    h.say("x" * 5000)

    assert len(h.model.calls[0][0]) <= 1000


def test_reschedule_with_no_upcoming_bookings_says_so() -> None:
    h = AgentHarness(Interpretation(action="reschedule", date="2026-10-06", time="10:00"))

    assert "no upcoming" in h.say("move my booking").lower()


def test_a_single_upcoming_booking_is_picked_without_asking() -> None:
    h = AgentHarness(Interpretation(action="reschedule", date="2026-10-06", time="10:00"))
    h.book_directly(ALICE, monday_local(10))

    reply = h.say("move my booking to tuesday 10am")

    assert "Tue 06 Oct 2026 at 10:00" in reply
    assert "yes" in reply.lower()


def test_several_bookings_need_the_customer_to_pick_by_number() -> None:
    h = AgentHarness(
        Interpretation(action="cancel"),
        Interpretation(action="other", booking_number=2),
    )
    h.book_directly(ALICE, monday_local(10))
    h.book_directly(ALICE, monday_local(12))

    ask = h.say("cancel my booking")
    reply = h.say("the second one")

    assert "1." in ask
    assert "2." in ask
    assert "12:00" in reply
    assert "yes" in reply.lower()


def test_a_booking_number_outside_my_list_is_ignored() -> None:
    h = AgentHarness(Interpretation(action="cancel", booking_number=7))
    h.book_directly(ALICE, monday_local(10))
    h.book_directly(ALICE, monday_local(12))

    reply = h.say("cancel booking 7")

    assert "1." in reply
    assert "2." in reply  # still asking which one
    assert all(b.status == "confirmed" for b in h.written().values())


def test_asking_about_a_closed_day_says_there_are_no_free_times() -> None:
    h = AgentHarness(
        Interpretation(action="availability", service_name="Haircut", date="2026-10-04")
    )

    assert "no free times" in h.say("anything free on sunday?").lower()
