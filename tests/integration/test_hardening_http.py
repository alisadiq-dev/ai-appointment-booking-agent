"""Security headers, request body size cap, and extreme-input handling."""

import json
import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.api.deps import get_booking_service, get_current_user
from app.core.hardening import MAX_BODY_BYTES
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

USER = uuid.uuid4()
HAIRCUT = make_service("Haircut", 30)
AS_USER = {"x-test-user": str(USER)}


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    hours = [
        BusinessHour(day_of_week=d, open_time=time(9), close_time=time(18)) for d in range(1, 8)
    ]
    service = BookingService(
        bookings=InMemoryBookingRepository(),
        services=InMemoryServiceRepository([HAIRCUT]),
        business_hours=InMemoryBusinessHoursRepository(hours),
        profiles=InMemoryProfileRepository({USER: "customer"}),
        calendar=FakeCalendar(),
        zone=ZoneInfo("Asia/Karachi"),
        slot_interval=timedelta(minutes=15),
        clock=lambda: datetime(2026, 10, 1, 8, 0, tzinfo=UTC),
    )

    def fake_user(request: Request) -> AuthUser:
        return AuthUser(id=uuid.UUID(request.headers["x-test-user"]))

    app.dependency_overrides[get_current_user] = fake_user
    app.dependency_overrides[get_booking_service] = lambda: service
    return TestClient(app, raise_server_exceptions=False)


def _error_code(response: Any) -> str:
    return response.json()["error"]["code"]


# ------------------------------------------------------------------ security headers


@pytest.mark.parametrize("path", ["/health", "/services", "/nope"])
def test_security_headers_are_on_every_response(client: TestClient, path: str) -> None:
    response = client.get(path, headers=AS_USER)

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"


def test_security_headers_are_on_a_500_too() -> None:
    app = create_app()

    @app.get("/_boom")
    def boom() -> None:
        raise RuntimeError("x")

    response = TestClient(app, raise_server_exceptions=False).get("/_boom")

    assert response.status_code == 500
    assert response.headers["x-content-type-options"] == "nosniff"


def test_the_interactive_docs_still_work(client: TestClient) -> None:
    # Documented decision: /docs stays enabled (the API is authenticated; Phase 8 checks it live).
    docs = client.get("/docs")
    assert docs.status_code == 200
    assert client.get("/openapi.json").status_code == 200


# ------------------------------------------------------------------ body size cap


def test_a_body_over_the_cap_is_413_in_the_standard_format(client: TestClient) -> None:
    body = json.dumps({"message": "x" * (MAX_BODY_BYTES + 1)})

    response = client.post(
        "/chat", content=body, headers={**AS_USER, "content-type": "application/json"}
    )

    assert response.status_code == 413
    assert _error_code(response) == "payload_too_large"


def test_a_chunked_body_over_the_cap_is_413_without_a_content_length(client: TestClient) -> None:
    def chunks() -> Any:
        for _ in range(10):
            yield b"x" * (MAX_BODY_BYTES // 4)

    response = client.post(
        "/chat", content=chunks(), headers={**AS_USER, "content-type": "application/json"}
    )

    assert response.status_code == 413
    assert _error_code(response) == "payload_too_large"


def test_a_normal_sized_body_is_not_affected(client: TestClient) -> None:
    response = client.post("/chat", json={"message": "x" * 1000}, headers=AS_USER)

    assert response.status_code != 413  # reaches validation / the agent, not the size cap


def test_the_cap_comfortably_fits_the_largest_valid_request() -> None:
    # 1000 chars of 4-byte characters, JSON-escaped as \uXXXX pairs (12 bytes each) worst case.
    worst_case = len(json.dumps({"message": "😀" * 1000}))
    assert worst_case < MAX_BODY_BYTES


# ------------------------------------------------------------------ extreme inputs


@pytest.mark.parametrize("day", ["0001-01-01", "9999-12-31", "1900-01-01", "2999-06-15"])
def test_availability_for_extreme_dates_is_a_clean_answer_not_a_500(
    client: TestClient, day: str
) -> None:
    response = client.get(f"/availability?service_id={HAIRCUT.id}&date={day}", headers=AS_USER)

    assert response.status_code < 500


@pytest.mark.parametrize(
    "start_at",
    [
        "0001-01-01T00:00:00Z",
        "0001-01-01T00:00:00+05:00",
        "9999-12-31T23:59:59Z",
        "9999-12-31T23:59:59-05:00",
        "2026-10-05T10:00:00+99:00",
        "not-a-date",
    ],
)
def test_booking_with_extreme_or_garbage_times_is_a_clean_4xx(
    client: TestClient, start_at: str
) -> None:
    response = client.post(
        "/bookings", json={"service_id": str(HAIRCUT.id), "start_at": start_at}, headers=AS_USER
    )

    assert 400 <= response.status_code < 500
    assert set(response.json()) == {"error"}


@pytest.mark.parametrize("path", ["/bookings/not-a-uuid", "/bookings/1; drop table bookings"])
def test_malformed_booking_ids_are_422_not_500(client: TestClient, path: str) -> None:
    response = client.post(f"{path}/cancel", headers=AS_USER)

    assert response.status_code in (404, 422)
    assert set(response.json()) == {"error"}
