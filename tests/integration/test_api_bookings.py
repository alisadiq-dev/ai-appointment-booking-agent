"""HTTP-level tests for the booking endpoints, backed by in-memory repositories."""

import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.api.deps import get_booking_service, get_current_user
from app.core.security import AuthUser
from app.main import create_app
from app.schemas.business_hours import BusinessHour
from app.services.booking_service import BookingService
from tests.fakes import (
    FakeCalendar,
    InMemoryBookingRepository,
    InMemoryBusinessHoursRepository,
    InMemoryProfileRepository,
    InMemoryServiceRepository,
    make_service,
)

ALICE = uuid.uuid4()
BOB = uuid.uuid4()
ADMIN = uuid.uuid4()
NOW = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
HAIRCUT = make_service("Haircut", 30)
COMBO = make_service("Haircut & Beard", 45)


def monday(hour: int, minute: int = 0) -> str:
    """ISO string for Monday 2026-10-05 (Karachi open 04:00Z-13:00Z)."""
    return datetime(2026, 10, 5, hour, minute, tzinfo=UTC).isoformat()


def _hours() -> list[BusinessHour]:
    rows = [
        BusinessHour(day_of_week=d, open_time=time(9), close_time=time(18)) for d in range(1, 6)
    ]
    rows.append(BusinessHour(day_of_week=6, open_time=time(10), close_time=time(16)))
    rows.append(BusinessHour(day_of_week=7, open_time=None, close_time=None))
    return rows


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    service = BookingService(
        bookings=InMemoryBookingRepository(),
        services=InMemoryServiceRepository([HAIRCUT, COMBO]),
        business_hours=InMemoryBusinessHoursRepository(_hours()),
        profiles=InMemoryProfileRepository({ALICE: "customer", BOB: "customer", ADMIN: "admin"}),
        calendar=FakeCalendar(),
        zone=ZoneInfo("Asia/Karachi"),
        slot_interval=timedelta(minutes=15),
        clock=lambda: NOW,
    )

    def fake_user(request: Request) -> AuthUser:
        return AuthUser(id=uuid.UUID(request.headers["x-test-user"]))

    app.dependency_overrides[get_current_user] = fake_user
    app.dependency_overrides[get_booking_service] = lambda: service
    return TestClient(app)


def as_user(user: uuid.UUID) -> dict[str, str]:
    return {"x-test-user": str(user)}


def error_of(response: Any) -> dict[str, str]:
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    return body["error"]


def create(client: TestClient, user: uuid.UUID, start: str, service: Any = HAIRCUT) -> Any:
    return client.post(
        "/bookings",
        json={"service_id": str(service.id), "start_at": start},
        headers=as_user(user),
    )


# ---------------------------------------------------------------------------- auth


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/services"),
        ("GET", "/business-hours"),
        ("GET", f"/availability?service_id={HAIRCUT.id}&date=2026-10-05"),
        ("POST", "/bookings"),
        ("GET", "/bookings"),
        ("PATCH", f"/bookings/{uuid.uuid4()}"),
        ("POST", f"/bookings/{uuid.uuid4()}/cancel"),
        ("GET", "/admin/bookings"),
    ],
)
def test_every_endpoint_requires_authentication(method: str, path: str) -> None:
    unauthenticated = TestClient(create_app())  # real auth dependency, no override

    response = unauthenticated.request(method, path)

    assert response.status_code == 401
    assert error_of(response)["code"] == "unauthorized"


# ----------------------------------------------------------------- services & hours


def test_list_services(client: TestClient) -> None:
    response = client.get("/services", headers=as_user(ALICE))

    assert response.status_code == 200
    assert response.json() == [
        {"id": str(HAIRCUT.id), "name": "Haircut", "duration_minutes": 30, "price": "25.00"},
        {"id": str(COMBO.id), "name": "Haircut & Beard", "duration_minutes": 45, "price": "25.00"},
    ]


def test_business_hours_include_the_timezone(client: TestClient) -> None:
    body = client.get("/business-hours", headers=as_user(ALICE)).json()

    assert body["timezone"] == "Asia/Karachi"
    assert body["hours"][0] == {"day_of_week": 1, "open_time": "09:00:00", "close_time": "18:00:00"}
    assert body["hours"][6] == {"day_of_week": 7, "open_time": None, "close_time": None}


# ---------------------------------------------------------------------- availability


def test_availability_lists_utc_slots(client: TestClient) -> None:
    response = client.get(
        f"/availability?service_id={COMBO.id}&date=2026-10-05", headers=as_user(ALICE)
    )

    body = response.json()
    assert response.status_code == 200
    assert body["service_id"] == str(COMBO.id)
    assert body["date"] == "2026-10-05"
    assert body["timezone"] == "Asia/Karachi"
    assert body["slots"][0] == {
        "start_at": "2026-10-05T04:00:00Z",
        "end_at": "2026-10-05T04:45:00Z",
    }


def test_availability_excludes_booked_slots(client: TestClient) -> None:
    create(client, ALICE, monday(5, 0))

    slots = client.get(
        f"/availability?service_id={HAIRCUT.id}&date=2026-10-05", headers=as_user(BOB)
    ).json()["slots"]

    assert "2026-10-05T05:00:00Z" not in [s["start_at"] for s in slots]


def test_availability_on_a_closed_day_is_empty(client: TestClient) -> None:
    response = client.get(
        f"/availability?service_id={HAIRCUT.id}&date=2026-10-04", headers=as_user(ALICE)
    )

    assert response.status_code == 200
    assert response.json()["slots"] == []


def test_availability_for_an_unknown_service_is_404(client: TestClient) -> None:
    response = client.get(
        f"/availability?service_id={uuid.uuid4()}&date=2026-10-05", headers=as_user(ALICE)
    )

    assert response.status_code == 404
    assert error_of(response)["code"] == "service_not_found"


@pytest.mark.parametrize(
    "query",
    [
        "",
        "?date=2026-10-05",
        f"?service_id={HAIRCUT.id}",
        "?service_id=nope&date=2026-10-05",
        f"?service_id={HAIRCUT.id}&date=not-a-date",
    ],
)
def test_availability_rejects_bad_query_params_in_the_standard_format(
    client: TestClient, query: str
) -> None:
    response = client.get(f"/availability{query}", headers=as_user(ALICE))

    assert response.status_code == 422
    assert error_of(response)["code"] == "validation_error"


# ------------------------------------------------------------------------- create


def test_create_booking_returns_201_without_internal_fields(client: TestClient) -> None:
    response = create(client, ALICE, monday(5, 0))

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "service_id", "start_at", "end_at", "status"}
    assert body["status"] == "confirmed"
    assert body["start_at"] == "2026-10-05T05:00:00Z"
    assert body["end_at"] == "2026-10-05T05:30:00Z"


def test_create_accepts_offsets_and_returns_utc(client: TestClient) -> None:
    response = create(client, ALICE, "2026-10-05T10:00:00+05:00")

    assert response.json()["start_at"] == "2026-10-05T05:00:00Z"


@pytest.mark.parametrize(
    "payload",
    [
        {"service_id": str(HAIRCUT.id), "start_at": "2026-10-05T05:00:00"},  # naive datetime
        {"service_id": str(HAIRCUT.id)},
        {"start_at": "2026-10-05T05:00:00Z"},
        {"service_id": "nope", "start_at": "2026-10-05T05:00:00Z"},
        {"service_id": str(HAIRCUT.id), "start_at": "2026-10-05T05:00:00Z", "user_id": str(BOB)},
    ],
)
def test_create_rejects_invalid_bodies(client: TestClient, payload: dict[str, str]) -> None:
    response = client.post("/bookings", json=payload, headers=as_user(ALICE))

    assert response.status_code == 422
    assert error_of(response)["code"] == "validation_error"


def test_a_validation_error_does_not_echo_the_submitted_value(client: TestClient) -> None:
    response = client.post(
        "/bookings",
        json={"service_id": "super-secret-value", "start_at": "2026-10-05T05:00:00Z"},
        headers=as_user(ALICE),
    )

    assert "super-secret-value" not in response.text


@pytest.mark.parametrize(
    ("start", "status", "code"),
    [
        ("2026-09-30T05:00:00Z", 422, "booking_in_past"),
        (monday(3, 0), 422, "outside_business_hours"),
        (monday(5, 7), 422, "invalid_slot_time"),
    ],
)
def test_create_business_rule_violations(
    client: TestClient, start: str, status: int, code: str
) -> None:
    response = create(client, ALICE, start)

    assert response.status_code == status
    assert error_of(response)["code"] == code


def test_create_overlapping_booking_is_409(client: TestClient) -> None:
    create(client, ALICE, monday(5, 0))

    response = create(client, BOB, monday(5, 15))

    assert response.status_code == 409
    assert error_of(response)["code"] == "slot_unavailable"


def test_create_with_an_unknown_service_is_404(client: TestClient) -> None:
    response = client.post(
        "/bookings",
        json={"service_id": str(uuid.uuid4()), "start_at": monday(5)},
        headers=as_user(ALICE),
    )

    assert response.status_code == 404
    assert error_of(response)["code"] == "service_not_found"


# --------------------------------------------------------------------------- list


def test_list_bookings_returns_only_the_callers(client: TestClient) -> None:
    mine = create(client, ALICE, monday(5, 0)).json()
    create(client, BOB, monday(7, 0))

    response = client.get("/bookings", headers=as_user(ALICE))

    assert response.status_code == 200
    assert [b["id"] for b in response.json()] == [mine["id"]]


# ------------------------------------------------------------------- reschedule


def test_reschedule_with_patch(client: TestClient) -> None:
    booking = create(client, ALICE, monday(5, 0)).json()

    response = client.patch(
        f"/bookings/{booking['id']}", json={"start_at": monday(7, 0)}, headers=as_user(ALICE)
    )

    assert response.status_code == 200
    assert response.json()["id"] == booking["id"]
    assert response.json()["start_at"] == "2026-10-05T07:00:00Z"


def test_reschedule_onto_its_own_overlapping_time_is_ok(client: TestClient) -> None:
    booking = create(client, ALICE, monday(5, 0)).json()

    response = client.patch(
        f"/bookings/{booking['id']}", json={"start_at": monday(5, 15)}, headers=as_user(ALICE)
    )

    assert response.status_code == 200


def test_reschedule_onto_another_bookings_time_is_409(client: TestClient) -> None:
    create(client, BOB, monday(5, 0))
    mine = create(client, ALICE, monday(7, 0)).json()

    response = client.patch(
        f"/bookings/{mine['id']}", json={"start_at": monday(5, 15)}, headers=as_user(ALICE)
    )

    assert response.status_code == 409
    assert error_of(response)["code"] == "slot_unavailable"


def test_reschedule_someone_elses_booking_is_404_not_403(client: TestClient) -> None:
    booking = create(client, ALICE, monday(5, 0)).json()

    response = client.patch(
        f"/bookings/{booking['id']}", json={"start_at": monday(7, 0)}, headers=as_user(BOB)
    )

    assert response.status_code == 404
    assert error_of(response)["code"] == "booking_not_found"


def test_reschedule_a_cancelled_booking_is_409_booking_not_active(client: TestClient) -> None:
    booking = create(client, ALICE, monday(5, 0)).json()
    client.post(f"/bookings/{booking['id']}/cancel", headers=as_user(ALICE))

    response = client.patch(
        f"/bookings/{booking['id']}", json={"start_at": monday(7, 0)}, headers=as_user(ALICE)
    )

    assert response.status_code == 409
    assert error_of(response)["code"] == "booking_not_active"


def test_reschedule_with_a_bad_booking_id_is_422(client: TestClient) -> None:
    response = client.patch(
        "/bookings/not-a-uuid", json={"start_at": monday(7)}, headers=as_user(ALICE)
    )

    assert response.status_code == 422
    assert error_of(response)["code"] == "validation_error"


# ----------------------------------------------------------------------- cancel


def test_cancel_keeps_the_row_and_changes_the_status(client: TestClient) -> None:
    booking = create(client, ALICE, monday(5, 0)).json()

    response = client.post(f"/bookings/{booking['id']}/cancel", headers=as_user(ALICE))

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert [b["status"] for b in client.get("/bookings", headers=as_user(ALICE)).json()] == [
        "cancelled"
    ]


def test_cancel_twice_is_409(client: TestClient) -> None:
    booking = create(client, ALICE, monday(5, 0)).json()
    client.post(f"/bookings/{booking['id']}/cancel", headers=as_user(ALICE))

    response = client.post(f"/bookings/{booking['id']}/cancel", headers=as_user(ALICE))

    assert response.status_code == 409
    assert error_of(response)["code"] == "booking_not_active"


def test_cancel_someone_elses_booking_is_404(client: TestClient) -> None:
    booking = create(client, ALICE, monday(5, 0)).json()

    response = client.post(f"/bookings/{booking['id']}/cancel", headers=as_user(BOB))

    assert response.status_code == 404


def test_delete_is_not_offered_for_bookings(client: TestClient) -> None:
    booking = create(client, ALICE, monday(5, 0)).json()

    assert client.delete(f"/bookings/{booking['id']}", headers=as_user(ALICE)).status_code == 405


# ------------------------------------------------------------------------ admin


def test_admin_sees_every_bookings_with_user_ids(client: TestClient) -> None:
    create(client, ALICE, monday(5, 0))
    create(client, BOB, monday(7, 0))

    response = client.get("/admin/bookings", headers=as_user(ADMIN))

    assert response.status_code == 200
    assert {b["user_id"] for b in response.json()} == {str(ALICE), str(BOB)}


def test_customers_cannot_use_the_admin_endpoint(client: TestClient) -> None:
    response = client.get("/admin/bookings", headers=as_user(ALICE))

    assert response.status_code == 403
    assert error_of(response)["code"] == "forbidden"


@pytest.mark.parametrize("query", ["?limit=0", "?limit=201", "?offset=-1", "?limit=abc"])
def test_admin_pagination_is_validated(client: TestClient, query: str) -> None:
    response = client.get(f"/admin/bookings{query}", headers=as_user(ADMIN))

    assert response.status_code == 422
    assert error_of(response)["code"] == "validation_error"


def test_admin_pagination_limits_results(client: TestClient) -> None:
    create(client, ALICE, monday(5, 0))
    create(client, BOB, monday(7, 0))

    response = client.get("/admin/bookings?limit=1", headers=as_user(ADMIN))

    assert len(response.json()) == 1


# --------------------------------------------------------------- active booking limit


def test_the_fourth_active_booking_is_409_in_the_standard_format(client: TestClient) -> None:
    for hour in (5, 6, 7):
        assert create(client, ALICE, monday(hour)).status_code == 201

    response = create(client, ALICE, monday(8))

    assert response.status_code == 409
    assert error_of(response)["code"] == "booking_limit_reached"
    assert len(client.get("/bookings", headers=as_user(ALICE)).json()) == 3


def test_the_limit_does_not_affect_other_users_or_rescheduling(client: TestClient) -> None:
    ids = [create(client, ALICE, monday(hour)).json()["id"] for hour in (5, 6, 7)]

    assert create(client, BOB, monday(8)).status_code == 201  # someone else is unaffected
    moved = client.patch(
        f"/bookings/{ids[0]}", json={"start_at": monday(9)}, headers=as_user(ALICE)
    )
    assert moved.status_code == 200  # reschedule is not a new booking
    cancelled = client.post(f"/bookings/{ids[1]}/cancel", headers=as_user(ALICE))
    assert cancelled.status_code == 200
    assert create(client, ALICE, monday(10)).status_code == 201  # a place was freed
