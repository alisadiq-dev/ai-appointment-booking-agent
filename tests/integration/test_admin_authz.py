"""Admin rights come only from profiles.role in the database, never from a token claim.

These tests use real signed tokens through the real TokenVerifier (no get_current_user override),
so the claims in the token are what the API actually sees.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app.api.deps import get_booking_service, get_token_verifier
from app.core.security import TokenVerifier
from app.main import create_app
from app.services.booking_service import BookingService
from tests.auth_helpers import (
    AUDIENCE,
    ISSUER,
    StaticKeyProvider,
    generate_es256_key,
    make_token,
)
from tests.fakes import (
    FakeCalendar,
    InMemoryBookingRepository,
    InMemoryBusinessHoursRepository,
    InMemoryProfileRepository,
    InMemoryServiceRepository,
)

CUSTOMER = uuid.uuid4()
ADMIN = uuid.uuid4()
NO_PROFILE = uuid.uuid4()

ADMIN_LOOKING_CLAIMS: list[dict[str, Any]] = [
    {"role": "admin"},
    {"role": "service_role"},
    {"role": "supabase_admin"},
    {"is_admin": True},
    {"admin": True},
    {"app_metadata": {"role": "admin", "is_admin": True}},
    {"user_metadata": {"role": "admin", "is_admin": True}},
    {"roles": ["admin"], "groups": ["admins"]},
]


@pytest.fixture(scope="module")
def private_key() -> ec.EllipticCurvePrivateKey:
    return generate_es256_key()


@pytest.fixture
def client(private_key: ec.EllipticCurvePrivateKey) -> TestClient:
    app = create_app()
    verifier = TokenVerifier(
        StaticKeyProvider(key=private_key.public_key()), issuer=ISSUER, audience=AUDIENCE
    )
    service = BookingService(
        bookings=InMemoryBookingRepository(),
        services=InMemoryServiceRepository([]),
        business_hours=InMemoryBusinessHoursRepository([]),
        profiles=InMemoryProfileRepository({CUSTOMER: "customer", ADMIN: "admin"}),
        calendar=FakeCalendar(),
        zone=ZoneInfo("Asia/Karachi"),
        slot_interval=timedelta(minutes=15),
        clock=lambda: datetime(2026, 10, 1, 8, 0, tzinfo=UTC),
    )
    app.dependency_overrides[get_token_verifier] = lambda: verifier
    app.dependency_overrides[get_booking_service] = lambda: service
    return TestClient(app)


def _get_admin(client: TestClient, token: str) -> Any:
    return client.get("/admin/bookings", headers={"Authorization": f"Bearer {token}"})


@pytest.mark.parametrize("claims", ADMIN_LOOKING_CLAIMS)
def test_admin_looking_claims_do_not_make_a_normal_user_an_admin(
    client: TestClient, private_key: ec.EllipticCurvePrivateKey, claims: dict[str, Any]
) -> None:
    token = make_token(private_key, sub=str(CUSTOMER), **claims)

    response = _get_admin(client, token)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_a_user_with_no_profile_is_forbidden_even_with_admin_claims(
    client: TestClient, private_key: ec.EllipticCurvePrivateKey
) -> None:
    token = make_token(private_key, sub=str(NO_PROFILE), role="admin", is_admin=True)

    assert _get_admin(client, token).status_code == 403


def test_the_database_role_is_what_grants_admin_whatever_the_token_says(
    client: TestClient, private_key: ec.EllipticCurvePrivateKey
) -> None:
    # A plain token (role=authenticated, no admin claims) for a profile whose role is admin.
    token = make_token(private_key, sub=str(ADMIN))

    response = _get_admin(client, token)

    assert response.status_code == 200
    assert response.json() == []
