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

from app.api.deps import get_calendar, get_current_user
from app.core.config import get_settings
from app.core.security import AuthUser
from app.main import create_app
from tests.fakes import FakeCalendar
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
    alice, bob = make_user(admin_conn, full_name="Alice Khan"), make_user(admin_conn)
    app = create_app()
    app.state.fake_calendar = calendar = FakeCalendar()
    app.dependency_overrides[get_calendar] = lambda: calendar
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


# ---------------------------------------- Google Calendar sync, with real transactions


def _calendar(client: TestClient) -> FakeCalendar:
    return client.app.state.fake_calendar  # type: ignore[attr-defined, no-any-return]


def _google_event_id(booking_id: str) -> str | None:
    assert DATABASE_URL
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        row = conn.execute(
            "select google_event_id from public.bookings where id = %s", (booking_id,)
        ).fetchone()
    return row["google_event_id"] if row else None


def test_creating_a_booking_creates_one_calendar_event_and_stores_its_id(
    stack: tuple[TestClient, uuid.UUID, uuid.UUID],
) -> None:
    client, _, _ = stack
    service = client.get("/services").json()[0]

    created = client.post(
        "/bookings", json={"service_id": service["id"], "start_at": MONDAY_5Z}
    ).json()

    booking_id = created["id"]
    assert _google_event_id(booking_id) == uuid.UUID(booking_id).hex
    [event] = _calendar(client).events.values()
    assert event.summary == f"{service['name']} - Alice Khan"
    assert event.description == f"Booking {booking_id}"


def test_a_google_failure_on_create_returns_503_and_leaves_no_booking(
    stack: tuple[TestClient, uuid.UUID, uuid.UUID],
) -> None:
    client, _, _ = stack
    service_id = client.get("/services").json()[0]["id"]
    _calendar(client).failing = {"create_event"}

    response = client.post("/bookings", json={"service_id": service_id, "start_at": MONDAY_5Z})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "calendar_unavailable"
    assert client.get("/bookings").json() == []  # really rolled back in Postgres

    _calendar(client).failing = set()  # Google recovers: the same slot is bookable again
    assert (
        client.post("/bookings", json={"service_id": service_id, "start_at": MONDAY_5Z}).status_code
        == 201
    )


def test_a_google_failure_on_cancel_keeps_the_booking_confirmed(
    stack: tuple[TestClient, uuid.UUID, uuid.UUID],
) -> None:
    client, _, _ = stack
    service_id = client.get("/services").json()[0]["id"]
    booking = client.post(
        "/bookings", json={"service_id": service_id, "start_at": MONDAY_5Z}
    ).json()
    _calendar(client).failing = {"delete_event"}

    response = client.post(f"/bookings/{booking['id']}/cancel")

    assert response.status_code == 503
    assert [b["status"] for b in client.get("/bookings").json()] == ["confirmed"]
    assert len(_calendar(client).events) == 1

    _calendar(client).failing = set()
    assert client.post(f"/bookings/{booking['id']}/cancel").json()["status"] == "cancelled"
    assert _calendar(client).events == {}


def test_a_google_failure_on_reschedule_leaves_the_booking_where_it_was(
    stack: tuple[TestClient, uuid.UUID, uuid.UUID],
) -> None:
    client, _, _ = stack
    service_id = client.get("/services").json()[0]["id"]
    booking = client.post(
        "/bookings", json={"service_id": service_id, "start_at": MONDAY_5Z}
    ).json()
    _calendar(client).failing = {"update_event"}

    response = client.patch(f"/bookings/{booking['id']}", json={"start_at": "2030-01-07T07:00:00Z"})

    assert response.status_code == 503
    assert client.get("/bookings").json()[0]["start_at"] == MONDAY_5Z


def test_availability_fails_closed_when_google_is_down(
    stack: tuple[TestClient, uuid.UUID, uuid.UUID],
) -> None:
    client, _, _ = stack
    service_id = client.get("/services").json()[0]["id"]
    _calendar(client).failing = {"list_busy"}

    response = client.get(f"/availability?service_id={service_id}&date=2030-01-07")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "calendar_unavailable"


def test_a_manual_calendar_event_blocks_booking_and_availability(
    stack: tuple[TestClient, uuid.UUID, uuid.UUID],
) -> None:
    from datetime import UTC, datetime

    from app.schemas.time_range import TimeRange

    client, _, _ = stack
    service_id = client.get("/services").json()[0]["id"]
    _calendar(client).manual_busy = [
        TimeRange(datetime(2030, 1, 7, 5, tzinfo=UTC), datetime(2030, 1, 7, 6, tzinfo=UTC))
    ]

    clash = client.post("/bookings", json={"service_id": service_id, "start_at": MONDAY_5Z})
    slots = client.get(f"/availability?service_id={service_id}&date=2030-01-07").json()["slots"]

    assert clash.status_code == 409
    assert clash.json()["error"]["code"] == "slot_unavailable"
    assert MONDAY_5Z not in [s["start_at"] for s in slots]
    assert "2030-01-07T06:00:00Z" in [s["start_at"] for s in slots]
    assert client.get("/bookings").json() == []
