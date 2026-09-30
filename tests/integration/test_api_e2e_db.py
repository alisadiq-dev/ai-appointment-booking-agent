"""Full stack over HTTP: routers -> service -> repositories -> real pool -> Postgres.

Data is committed (separate requests use separate pooled connections), so the test creates its
own users and deletes them afterwards.
"""

import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.security import AuthUser
from app.main import create_app
from tests.integration.conftest import DATABASE_URL, make_user, requires_db

pytestmark = [pytest.mark.local_supabase, requires_db]

# 2030-01-07 is a Monday; default business timezone Asia/Karachi (UTC+5), open 04:00Z-13:00Z.
MONDAY_5Z = "2030-01-07T05:00:00Z"


@pytest.fixture
def stack(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, uuid.UUID, uuid.UUID]]:
    assert DATABASE_URL
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("BUSINESS_TIMEZONE", "Asia/Karachi")
    get_settings.cache_clear()
    admin_conn: psycopg.Connection[dict[str, Any]] = psycopg.connect(
        DATABASE_URL, row_factory=dict_row, autocommit=True
    )
    alice, bob = make_user(admin_conn), make_user(admin_conn)
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthUser(id=current["user"])
    current = {"user": alice}
    try:
        with TestClient(app) as client:
            yield client, alice, bob
    finally:
        admin_conn.execute("delete from auth.users where id = any(%s)", ([alice, bob],))
        admin_conn.close()
        get_settings.cache_clear()


def test_booking_lifecycle_persists_across_requests(
    stack: tuple[TestClient, uuid.UUID, uuid.UUID],
) -> None:
    client, _, _ = stack
    service_id = client.get("/services").json()[0]["id"]

    created = client.post("/bookings", json={"service_id": service_id, "start_at": MONDAY_5Z})
    assert created.status_code == 201  # committed when the request succeeded

    listed = client.get("/bookings").json()  # a different pooled connection
    assert [b["id"] for b in listed] == [created.json()["id"]]

    moved = client.patch(
        f"/bookings/{created.json()['id']}", json={"start_at": "2030-01-07T07:00:00Z"}
    )
    assert moved.json()["start_at"] == "2030-01-07T07:00:00Z"

    cancelled = client.post(f"/bookings/{created.json()['id']}/cancel")
    assert cancelled.json()["status"] == "cancelled"
    assert client.get("/bookings").json()[0]["status"] == "cancelled"


def test_a_conflict_returns_409_and_does_not_break_the_next_request(
    stack: tuple[TestClient, uuid.UUID, uuid.UUID],
) -> None:
    client, alice, bob = stack
    service_id = client.get("/services").json()[0]["id"]
    app_overrides = client.app.dependency_overrides  # type: ignore[attr-defined]
    client.post("/bookings", json={"service_id": service_id, "start_at": MONDAY_5Z})

    app_overrides[get_current_user] = lambda: AuthUser(id=bob)
    clash = client.post(
        "/bookings", json={"service_id": service_id, "start_at": "2030-01-07T05:15:00Z"}
    )

    assert clash.status_code == 409
    assert clash.json()["error"]["code"] == "slot_unavailable"
    assert client.get("/bookings").json() == []  # bob has nothing; pool connection is healthy

    app_overrides[get_current_user] = lambda: AuthUser(id=alice)
    assert len(client.get("/bookings").json()) == 1  # alice's booking survived


def test_users_cannot_see_each_others_bookings_over_http(
    stack: tuple[TestClient, uuid.UUID, uuid.UUID],
) -> None:
    client, _, bob = stack
    service_id = client.get("/services").json()[0]["id"]
    booking = client.post(
        "/bookings", json={"service_id": service_id, "start_at": MONDAY_5Z}
    ).json()

    client.app.dependency_overrides[get_current_user] = lambda: AuthUser(id=bob)  # type: ignore[attr-defined]

    assert client.get("/bookings").json() == []
    assert client.post(f"/bookings/{booking['id']}/cancel").status_code == 404
    assert (
        client.patch(
            f"/bookings/{booking['id']}", json={"start_at": "2030-01-07T07:00:00Z"}
        ).status_code
        == 404
    )


def test_availability_reads_real_hours_and_bookings(
    stack: tuple[TestClient, uuid.UUID, uuid.UUID],
) -> None:
    client, _, _ = stack
    service = client.get("/services").json()[0]
    client.post("/bookings", json={"service_id": service["id"], "start_at": MONDAY_5Z})

    slots = client.get(f"/availability?service_id={service['id']}&date=2030-01-07").json()["slots"]

    starts = [s["start_at"] for s in slots]
    assert starts[0] == "2030-01-07T04:00:00Z"
    assert MONDAY_5Z not in starts
