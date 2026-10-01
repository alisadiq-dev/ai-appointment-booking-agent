"""POST /chat over HTTP against real Postgres: one transaction per turn, session saved only when
the turn succeeds, business outcomes as 200, per-user turn lock. Gemini is a scripted model."""

import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app.agents.model import Interpretation, ModelContext
from app.api.deps import get_calendar, get_chat_rate_limiter, get_current_user, get_language_model
from app.core.config import get_settings
from app.core.rate_limit import SlidingWindowRateLimiter
from app.core.security import AuthUser
from app.main import create_app
from tests.agent_helpers import ScriptedModel
from tests.fakes import FakeCalendar
from tests.integration.conftest import DATABASE_URL, make_user, requires_db

pytestmark = [pytest.mark.local_supabase, requires_db]

# 2030-01-07 is a Monday; Karachi (UTC+5), open 09:00-18:00 local.
BOOK_10 = Interpretation(action="book", service_name="Haircut", date="2030-01-07", time="10:00")


class BlockingModel:
    """Holds the first interpret() call until released, to keep one turn in flight."""

    def __init__(self, inner: ScriptedModel) -> None:
        self.inner = inner
        self.entered = threading.Event()
        self.release = threading.Event()

    def interpret(self, message: str, context: ModelContext) -> Interpretation:
        self.entered.set()
        assert self.release.wait(timeout=15)
        return self.inner.interpret(message, context)


@dataclass
class Stack:
    client: TestClient
    alice: uuid.UUID
    bob: uuid.UUID
    calendar: FakeCalendar
    model: ScriptedModel
    admin: "psycopg.Connection[dict[str, Any]]"
    current: dict[str, uuid.UUID]

    def say(self, message: str, user: uuid.UUID | None = None) -> Any:
        self.current["user"] = user or self.alice
        return self.client.post("/chat", json={"message": message})

    def bookings(self, user: uuid.UUID) -> list[dict[str, Any]]:
        return self.admin.execute(
            "select id, status, start_at from public.bookings where user_id = %s", (user,)
        ).fetchall()

    def session(self, user: uuid.UUID) -> dict[str, Any] | None:
        row = self.admin.execute(
            "select state from public.conversation_sessions where user_id = %s", (user,)
        ).fetchone()
        return row["state"] if row else None


@pytest.fixture
def make_stack(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    assert DATABASE_URL
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("BUSINESS_TIMEZONE", "Asia/Karachi")
    get_settings.cache_clear()
    admin: psycopg.Connection[dict[str, Any]] = psycopg.connect(
        DATABASE_URL, row_factory=dict_row, autocommit=True
    )
    alice, bob = make_user(admin, full_name="Alice Khan"), make_user(admin)
    created: list[Any] = []

    def build(*script: Interpretation | Exception, model: Any = None) -> Stack:
        scripted = ScriptedModel(*script)
        calendar = FakeCalendar()
        current = {"user": alice}
        app = create_app()
        app.dependency_overrides[get_calendar] = lambda: calendar
        app.dependency_overrides[get_current_user] = lambda: AuthUser(id=current["user"])
        app.dependency_overrides[get_language_model] = lambda: model or scripted
        limiter = SlidingWindowRateLimiter(limit=1000, window_seconds=60)
        app.dependency_overrides[get_chat_rate_limiter] = lambda: limiter
        client = TestClient(app)
        client.__enter__()
        created.append(client)
        return Stack(client, alice, bob, calendar, scripted, admin, current)

    try:
        yield build
    finally:
        for client in created:
            client.__exit__(None, None, None)
        admin.execute("delete from auth.users where id = any(%s)", ([alice, bob],))
        admin.close()
        get_settings.cache_clear()


# ------------------------------------------------------------------ one transaction, one commit


def test_propose_then_yes_persists_across_requests_and_resets_the_session(make_stack: Any) -> None:
    s: Stack = make_stack(BOOK_10)

    proposal = s.say("book a haircut monday 10am")

    assert proposal.status_code == 200
    assert "yes to confirm" in proposal.json()["reply"].lower()
    assert s.bookings(s.alice) == []  # a proposal writes no booking
    assert s.session(s.alice)["pending"] is not None  # but the session was saved (committed)

    done = s.say("yes")

    assert done.status_code == 200
    [booking] = s.bookings(s.alice)
    assert booking["status"] == "confirmed"
    assert s.session(s.alice) is None  # reset after completion
    assert len(s.calendar.events) == 1


def test_a_calendar_failure_rolls_back_the_turn_and_keeps_the_proposal(make_stack: Any) -> None:
    s: Stack = make_stack(BOOK_10)
    s.say("book a haircut monday 10am")
    pending_before = s.session(s.alice)
    s.calendar.failing = {"create_event"}

    failed = s.say("yes")

    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "calendar_unavailable"
    assert s.bookings(s.alice) == []  # the booking insert was rolled back
    assert s.session(s.alice) == pending_before  # session neither saved nor cleared
    s.calendar.failing = set()

    retried = s.say("yes")  # the customer simply confirms again

    assert retried.status_code == 200
    assert len(s.bookings(s.alice)) == 1
    assert len(s.calendar.events) == 1


def test_a_failed_reschedule_turn_leaves_the_booking_where_it_was(make_stack: Any) -> None:
    s: Stack = make_stack(
        BOOK_10, Interpretation(action="reschedule", date="2030-01-08", time="11:00")
    )
    s.say("book a haircut monday 10am")
    s.say("yes")
    [before] = s.bookings(s.alice)
    s.say("move my booking to tuesday 11am")
    s.calendar.failing = {"update_event"}

    failed = s.say("yes")

    assert failed.status_code == 503
    [after] = s.bookings(s.alice)
    assert after["start_at"] == before["start_at"]


def test_a_booking_whose_calendar_event_was_deleted_by_hand_is_409_with_next_steps(
    make_stack: Any,
) -> None:
    s: Stack = make_stack(
        BOOK_10,
        Interpretation(action="reschedule", date="2030-01-08", time="11:00"),
        Interpretation(action="cancel"),
    )
    s.say("book a haircut monday 10am")
    s.say("yes")
    [before] = s.bookings(s.alice)
    s.calendar.gone = set(s.calendar.events)  # someone deleted the Google event manually
    s.say("move my booking to tuesday 11am")

    failed = s.say("yes")

    assert failed.status_code == 409
    error = failed.json()["error"]
    assert error["code"] == "calendar_event_missing"
    assert "cancel it and book again" in error["message"]
    [after] = s.bookings(s.alice)
    assert after["start_at"] == before["start_at"]  # rolled back, not half-moved

    # Recovery as the message says: answer the open question with no, cancel, then book again.
    assert s.say("no").status_code == 200
    s.say("cancel my booking")
    assert s.say("yes").status_code == 200
    assert [b["status"] for b in s.bookings(s.alice)] == ["cancelled"]


# ------------------------------------------------------------------ business outcomes are 200


def test_a_slot_taken_between_proposal_and_yes_is_a_200_with_fresh_slots(make_stack: Any) -> None:
    s: Stack = make_stack(BOOK_10, BOOK_10)
    s.say("book a haircut monday 10am")
    s.say("book a haircut monday 10am", user=s.bob)  # bob proposes the same slot...
    assert s.say("yes", user=s.bob).status_code == 200  # ...and gets it first

    reply = s.say("yes")

    assert reply.status_code == 200
    assert "no longer available" in reply.json()["reply"]
    assert "Free times that day" in reply.json()["reply"]
    assert s.bookings(s.alice) == []
    assert s.session(s.alice)["pending"] is None


def test_a_booking_cancelled_meanwhile_is_a_200(make_stack: Any) -> None:
    s: Stack = make_stack(
        BOOK_10, Interpretation(action="cancel"), Interpretation(action="reschedule")
    )
    s.say("book a haircut monday 10am")
    s.say("yes")
    [booking] = s.bookings(s.alice)
    s.say("cancel my booking")  # proposal pending
    s.admin.execute(  # cancelled from somewhere else in the meantime
        "update public.bookings set status = 'cancelled' where id = %s", (booking["id"],)
    )

    reply = s.say("yes")

    assert reply.status_code == 200
    assert "no longer active" in reply.json()["reply"]


# ------------------------------------------------------------------ isolation


def test_one_user_cannot_confirm_another_users_proposal(make_stack: Any) -> None:
    s: Stack = make_stack(BOOK_10)
    s.say("book a haircut monday 10am")

    reply = s.say("yes", user=s.bob)

    assert "nothing waiting" in reply.json()["reply"].lower()
    assert s.bookings(s.alice) == []
    assert s.bookings(s.bob) == []
    assert s.session(s.alice)["pending"] is not None  # alice's proposal is untouched


# ------------------------------------------------------------------ concurrent turns


def test_a_second_simultaneous_turn_for_the_same_user_fails_fast_with_409(make_stack: Any) -> None:
    scripted = ScriptedModel(BOOK_10, Interpretation(action="other"))
    blocking = BlockingModel(scripted)
    s: Stack = make_stack(model=blocking)
    first_result: dict[str, Any] = {}

    def first_turn() -> None:
        first_result["response"] = s.client.post("/chat", json={"message": "book a haircut"})

    s.current["user"] = s.alice
    worker = threading.Thread(target=first_turn)
    worker.start()
    try:
        assert blocking.entered.wait(timeout=15)  # turn 1 holds the lock and is inside the model

        second = s.client.post("/chat", json={"message": "hello again"})

        assert second.status_code == 409
        assert second.json()["error"]["code"] == "turn_in_progress"
        assert len(scripted.calls) == 0  # the rejected turn never reached the model
    finally:
        blocking.release.set()
        worker.join(timeout=15)

    assert first_result["response"].status_code == 200  # the first turn was unharmed
    assert "yes to confirm" in first_result["response"].json()["reply"].lower()
    third = s.client.post("/chat", json={"message": "hello"})
    assert third.status_code == 200  # the lock was released with the transaction


def test_another_user_is_not_blocked_by_a_turn_in_flight(make_stack: Any) -> None:
    scripted = ScriptedModel(BOOK_10, Interpretation(action="other"))
    blocking = BlockingModel(scripted)
    s: Stack = make_stack(model=blocking)
    worker = threading.Thread(
        target=lambda: s.client.post("/chat", json={"message": "book a haircut"})
    )
    s.current["user"] = s.alice
    worker.start()
    try:
        assert blocking.entered.wait(timeout=15)
        blocking.release.set()  # let any later model call through immediately
        s.current["user"] = s.bob

        other = s.client.post("/chat", json={"message": "hello"})

        assert other.status_code != 409
    finally:
        blocking.release.set()
        worker.join(timeout=15)
