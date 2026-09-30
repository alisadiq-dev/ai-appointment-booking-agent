"""Deadlock handling in BookingRepository, with a scripted connection (no database).

Under truly simultaneous conflicting inserts Postgres may abort one transaction with
deadlock_detected (40P01) instead of exclusion_violation (23P01). Nobody is double-booked,
but the loser must get a clean "slot taken" (or a retry), never a 500.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg.errors
import pytest

from app.repositories.bookings import BookingRepository
from app.repositories.errors import SlotTakenError

START = datetime(2030, 1, 7, 5, 0, tzinfo=UTC)
END = START + timedelta(minutes=30)


def _row() -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "service_id": uuid.uuid4(),
        "start_at": START,
        "end_at": END,
        "status": "confirmed",
        "google_event_id": None,
    }


class _Cursor:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def fetchone(self) -> dict[str, Any] | None:
        return self._row


class ScriptedConn:
    """Plays back outcomes: an Exception is raised, anything else is returned as the row."""

    def __init__(self, *outcomes: Any) -> None:
        self._outcomes = list(outcomes)
        self.executes = 0  # data statements (the advisory lock is counted separately)
        self.locks = 0
        self.transaction_blocks = 0

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.transaction_blocks += 1
        yield

    def execute(self, query: str, *_: Any) -> _Cursor:
        if "pg_advisory_xact_lock" in query:
            self.locks += 1
            return _Cursor(None)
        self.executes += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return _Cursor(outcome)


def _create(conn: ScriptedConn) -> Any:
    return BookingRepository(conn).create(uuid.uuid4(), uuid.uuid4(), START, END)  # type: ignore[arg-type]


def _update(conn: ScriptedConn) -> Any:
    return BookingRepository(conn).update_times(uuid.uuid4(), uuid.uuid4(), START, END)  # type: ignore[arg-type]


def test_create_retries_once_after_a_deadlock_and_returns_the_booking() -> None:
    conn = ScriptedConn(psycopg.errors.DeadlockDetected("deadlock"), _row())

    booking = _create(conn)

    assert booking.status == "confirmed"
    assert conn.executes == 2
    assert conn.transaction_blocks == 2  # each attempt in its own (sub)transaction


def test_create_reports_slot_taken_when_the_retry_hits_the_exclusion_constraint() -> None:
    conn = ScriptedConn(
        psycopg.errors.DeadlockDetected("deadlock"), psycopg.errors.ExclusionViolation("overlap")
    )

    with pytest.raises(SlotTakenError):
        _create(conn)


def test_create_reports_slot_taken_if_it_deadlocks_twice() -> None:
    conn = ScriptedConn(
        psycopg.errors.DeadlockDetected("deadlock"), psycopg.errors.DeadlockDetected("deadlock")
    )

    with pytest.raises(SlotTakenError):
        _create(conn)
    assert conn.executes == 2  # exactly one retry, no loop


def test_create_does_not_retry_a_plain_exclusion_violation() -> None:
    conn = ScriptedConn(psycopg.errors.ExclusionViolation("overlap"))

    with pytest.raises(SlotTakenError):
        _create(conn)
    assert conn.executes == 1


def test_create_does_not_swallow_unrelated_database_errors() -> None:
    conn = ScriptedConn(psycopg.errors.ForeignKeyViolation("bad service"))

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _create(conn)


def test_update_times_retries_once_after_a_deadlock() -> None:
    conn = ScriptedConn(psycopg.errors.DeadlockDetected("deadlock"), _row())

    assert _update(conn) is not None
    assert conn.executes == 2


def test_update_times_reports_slot_taken_if_it_deadlocks_twice() -> None:
    conn = ScriptedConn(
        psycopg.errors.DeadlockDetected("deadlock"), psycopg.errors.DeadlockDetected("deadlock")
    )

    with pytest.raises(SlotTakenError):
        _update(conn)


def test_update_times_returns_none_when_no_row_matches() -> None:
    assert _update(ScriptedConn(None)) is None


def test_every_write_attempt_takes_the_booking_write_lock_first() -> None:
    # Serializing booking writes makes competing writers queue instead of deadlocking.
    conn = ScriptedConn(psycopg.errors.DeadlockDetected("deadlock"), _row())

    _create(conn)

    assert conn.locks == 2  # one per attempt, inside each transaction block


def test_update_times_takes_the_booking_write_lock() -> None:
    conn = ScriptedConn(_row())

    _update(conn)

    assert conn.locks == 1
