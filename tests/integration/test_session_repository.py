import pytest

from app.repositories.sessions import SessionRepository
from tests.integration.conftest import Conn, make_user, requires_db

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
