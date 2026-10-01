import uuid

import psycopg
import pytest
from psycopg.rows import dict_row

from app.repositories.bookings import BOOKING_WRITE_LOCK_KEY
from app.repositories.sessions import CHAT_TURN_LOCK_NAMESPACE, SessionRepository
from tests.integration.conftest import DATABASE_URL, Conn, make_user, requires_db

pytestmark = [pytest.mark.local_supabase, requires_db]


def test_a_user_with_no_session_has_empty_state(conn: Conn) -> None:
    assert SessionRepository(conn).load(make_user(conn)) == {}


def test_save_then_load_round_trips_json(conn: Conn) -> None:
    repo = SessionRepository(conn)
    user = make_user(conn)

    repo.save(user, {"draft": {"action": "book"}, "pending": None})

    assert repo.load(user) == {"draft": {"action": "book"}, "pending": None}


def test_save_twice_keeps_one_row_with_the_latest_state(conn: Conn) -> None:
    repo = SessionRepository(conn)
    user = make_user(conn)

    repo.save(user, {"n": 1})
    repo.save(user, {"n": 2})

    assert repo.load(user) == {"n": 2}
    count = conn.execute(
        "select count(*) as n from public.conversation_sessions where user_id = %s", (user,)
    ).fetchone()
    assert count is not None
    assert count["n"] == 1


def test_clear_removes_the_state(conn: Conn) -> None:
    repo = SessionRepository(conn)
    user = make_user(conn)
    repo.save(user, {"n": 1})

    repo.clear(user)

    assert repo.load(user) == {}


def test_users_never_see_each_others_state(conn: Conn) -> None:
    repo = SessionRepository(conn)
    alice, bob = make_user(conn), make_user(conn)
    repo.save(alice, {"owner": "alice"})

    assert repo.load(bob) == {}
    repo.clear(bob)
    assert repo.load(alice) == {"owner": "alice"}


# ------------------------------------------------------------------ per-user turn lock


def test_turn_lock_uses_a_dedicated_two_key_namespace(conn: Conn) -> None:
    repo = SessionRepository(conn)

    assert repo.try_lock_turn(make_user(conn)) is True

    held = conn.execute(
        "select classid, objsubid from pg_locks "
        "where locktype = 'advisory' and pid = pg_backend_pid()"
    ).fetchall()
    assert [(row["classid"], row["objsubid"]) for row in held] == [(CHAT_TURN_LOCK_NAMESPACE, 2)]
    assert CHAT_TURN_LOCK_NAMESPACE != BOOKING_WRITE_LOCK_KEY  # and a different lock form


def test_a_second_connection_cannot_take_the_same_users_turn_lock() -> None:
    assert DATABASE_URL
    first = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    second = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    user = uuid.uuid4()  # no database row needed: the lock is just a key
    try:
        assert SessionRepository(first).try_lock_turn(user) is True
        assert SessionRepository(second).try_lock_turn(user) is False  # fails fast, no waiting
        assert SessionRepository(second).try_lock_turn(uuid.uuid4()) is True  # other users: free

        first.commit()  # the lock is released with the transaction

        assert SessionRepository(second).try_lock_turn(user) is True
    finally:
        first.rollback()
        second.rollback()
        first.close()
        second.close()


def test_the_turn_lock_is_released_on_rollback_too() -> None:
    assert DATABASE_URL
    first = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    second = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    user = uuid.uuid4()
    try:
        assert SessionRepository(first).try_lock_turn(user) is True

        first.rollback()

        assert SessionRepository(second).try_lock_turn(user) is True
    finally:
        second.rollback()
        first.close()
        second.close()
