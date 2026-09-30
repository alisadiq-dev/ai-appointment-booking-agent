import os
import uuid
from collections.abc import Iterator
from decimal import Decimal

import psycopg
import pytest
from psycopg.rows import dict_row

DATABASE_URL = os.environ.get("DATABASE_URL")

requires_db = pytest.mark.skipif(
    not DATABASE_URL, reason="set DATABASE_URL (local Supabase) to run database tests"
)

from app.repositories.types import Conn  # noqa: E402


@pytest.fixture
def conn() -> Iterator[Conn]:
    """A connection whose work is rolled back after each test (no cleanup needed)."""
    assert DATABASE_URL
    connection: Conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    yield connection
    connection.rollback()
    connection.close()


def make_user(conn: Conn, *, role: str = "customer") -> uuid.UUID:
    """Insert an auth user (the signup trigger creates the profile) and set its role."""
    user_id = uuid.uuid4()
    conn.execute(
        "insert into auth.users (id, email) values (%s, %s)",
        (user_id, f"{user_id}@example.test"),
    )
    if role != "customer":
        conn.execute("update public.profiles set role = %s where id = %s", (role, user_id))
    return user_id


def make_service(conn: Conn, *, minutes: int = 30, name: str | None = None) -> uuid.UUID:
    service_id = uuid.uuid4()
    conn.execute(
        "insert into public.services (id, name, duration_minutes, price) values (%s, %s, %s, %s)",
        (service_id, name or f"test-{service_id}", minutes, Decimal("10.00")),
    )
    return service_id
