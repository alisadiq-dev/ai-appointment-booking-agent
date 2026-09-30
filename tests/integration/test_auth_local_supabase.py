"""Verifies a REAL token issued by a local Supabase GoTrue against its real JWKS endpoint.

Opt-in: skipped unless SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY are set (fails instead
when REQUIRE_DB=1). Needs `supabase start`
with asymmetric signing keys enabled (see docs/local-supabase.md).
"""

import json
import os
import urllib.request
import uuid
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import CurrentUser, get_token_verifier
from app.core.config import get_settings
from app.main import create_app

SUPABASE_URL = os.environ.get("SUPABASE_URL")
PUBLISHABLE_KEY = os.environ.get("SUPABASE_PUBLISHABLE_KEY")

pytestmark = [pytest.mark.local_supabase, pytest.mark.requires_supabase_auth]


def _sign_up() -> tuple[str, str]:
    """Create a throwaway user in local GoTrue; returns (access_token, user_id)."""
    request = urllib.request.Request(  # noqa: S310 - local dev URL from env
        f"{SUPABASE_URL}/auth/v1/signup",
        data=json.dumps(
            {"email": f"pytest-{uuid.uuid4().hex[:12]}@example.test", "password": uuid.uuid4().hex}
        ).encode(),
        headers={"apikey": PUBLISHABLE_KEY or "", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        body = json.load(response)
    return body["access_token"], body["user"]["id"]


@pytest.fixture
def client() -> Iterator[TestClient]:
    get_settings.cache_clear()
    get_token_verifier.cache_clear()
    app: FastAPI = create_app()

    @app.get("/_probe")
    def probe(user: CurrentUser) -> dict[str, str]:
        return {"user_id": str(user.id)}

    yield TestClient(app)
    get_settings.cache_clear()
    get_token_verifier.cache_clear()


def test_real_gotrue_token_is_accepted(client: TestClient) -> None:
    token, user_id = _sign_up()

    response = client.get("/_probe", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == {"user_id": user_id}


def test_real_gotrue_token_with_tampered_payload_is_rejected(client: TestClient) -> None:
    token, _ = _sign_up()
    header, payload, signature = token.split(".")
    tampered = f"{header}.{payload[:-2]}AA.{signature}"

    response = client.get("/_probe", headers={"Authorization": f"Bearer {tampered}"})

    assert response.status_code == 401
