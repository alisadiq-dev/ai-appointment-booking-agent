import logging
import time
from collections.abc import Iterator
from typing import Annotated

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.deps import CurrentUser, get_current_user, get_token_verifier
from app.core.config import get_settings
from app.core.security import AuthUnavailableError, AuthUser, TokenVerifier, UnauthorizedError
from app.main import create_app
from tests.auth_helpers import (
    AUDIENCE,
    ISSUER,
    KID,
    USER_ID,
    StaticKeyProvider,
    forge_unsigned_token,
    generate_es256_key,
    jwks_document,
    make_token,
)
from tests.fake_jwks_server import FakeJwksServer

UNAUTHORIZED_BODY = {
    "error": {"code": "unauthorized", "message": "Invalid or missing authentication token."}
}


def _add_probe_route(app: FastAPI) -> FastAPI:
    @app.get("/_probe")
    def probe(user: CurrentUser) -> dict[str, str]:
        return {"user_id": str(user.id)}

    return app


@pytest.fixture(scope="module")
def private_key() -> ec.EllipticCurvePrivateKey:
    return generate_es256_key()


@pytest.fixture
def client(private_key: ec.EllipticCurvePrivateKey) -> TestClient:
    app = _add_probe_route(create_app())
    verifier = TokenVerifier(
        StaticKeyProvider(key=private_key.public_key()), issuer=ISSUER, audience=AUDIENCE
    )
    app.dependency_overrides[get_token_verifier] = lambda: verifier
    return TestClient(app)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_valid_token_reaches_the_route_with_the_user(
    client: TestClient, private_key: ec.EllipticCurvePrivateKey
) -> None:
    response = client.get("/_probe", headers=_bearer(make_token(private_key)))

    assert response.status_code == 200
    assert response.json() == {"user_id": USER_ID}


def test_missing_authorization_header_is_401_in_the_standard_format(client: TestClient) -> None:
    response = client.get("/_probe")

    assert response.status_code == 401
    assert response.json() == UNAUTHORIZED_BODY
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("header", ["Basic dXNlcjpwYXNz", "Bearer", "Bearer ", "Token abc", "abc"])
def test_wrong_scheme_or_empty_token_is_401(client: TestClient, header: str) -> None:
    response = client.get("/_probe", headers={"Authorization": header})

    assert response.status_code == 401
    assert response.json() == UNAUTHORIZED_BODY


def test_expired_token_is_401(client: TestClient, private_key: ec.EllipticCurvePrivateKey) -> None:
    token = make_token(private_key, exp=int(time.time()) - 3600)

    response = client.get("/_probe", headers=_bearer(token))

    assert response.status_code == 401
    assert response.json() == UNAUTHORIZED_BODY


def test_all_rejections_look_identical_to_the_caller(
    client: TestClient, private_key: ec.EllipticCurvePrivateKey
) -> None:
    bad_tokens = [
        make_token(private_key, exp=int(time.time()) - 3600),  # expired
        make_token(generate_es256_key()),  # wrong signature
        make_token(private_key, aud="other"),  # wrong audience
        forge_unsigned_token(),  # alg=none
        "garbage",
    ]

    bodies = [client.get("/_probe", headers=_bearer(t)).json() for t in bad_tokens]

    assert all(body == UNAUTHORIZED_BODY for body in bodies)


def test_unavailable_key_source_is_503_not_401(private_key: ec.EllipticCurvePrivateKey) -> None:
    app = _add_probe_route(create_app())
    verifier = TokenVerifier(
        StaticKeyProvider(error=AuthUnavailableError()), issuer=ISSUER, audience=AUDIENCE
    )
    app.dependency_overrides[get_token_verifier] = lambda: verifier

    response = TestClient(app).get("/_probe", headers=_bearer(make_token(private_key)))

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "auth_unavailable",
            "message": "Authentication is temporarily unavailable.",
        }
    }


def test_unknown_key_is_401(private_key: ec.EllipticCurvePrivateKey) -> None:
    app = _add_probe_route(create_app())
    verifier = TokenVerifier(
        StaticKeyProvider(error=UnauthorizedError()), issuer=ISSUER, audience=AUDIENCE
    )
    app.dependency_overrides[get_token_verifier] = lambda: verifier

    response = TestClient(app).get("/_probe", headers=_bearer(make_token(private_key)))

    assert response.status_code == 401


def test_health_stays_public(client: TestClient) -> None:
    assert client.get("/health").status_code == 200


def test_authenticated_route_dependency_can_be_used_directly() -> None:
    app = create_app()

    @app.get("/_direct")
    def direct(user: Annotated[AuthUser, Depends(get_current_user)]) -> dict[str, str]:
        return {"user_id": str(user.id)}

    assert TestClient(app).get("/_direct").status_code == 401


# ---- real settings -> real JwksKeyProvider, no dependency overrides ----


@pytest.fixture
def fresh_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    get_settings.cache_clear()
    get_token_verifier.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()
    get_token_verifier.cache_clear()


def test_end_to_end_with_settings_and_a_jwks_endpoint(
    fresh_settings: pytest.MonkeyPatch, private_key: ec.EllipticCurvePrivateKey
) -> None:
    server = FakeJwksServer(jwks_document((KID, private_key)))
    server.start()
    try:
        base_url = server.url.split("/auth/v1/")[0]
        fresh_settings.setenv("SUPABASE_URL", base_url)
        app = _add_probe_route(create_app())
        token = make_token(private_key, iss=f"{base_url}/auth/v1")

        response = TestClient(app).get("/_probe", headers=_bearer(token))

        assert response.status_code == 200
        assert response.json() == {"user_id": USER_ID}
    finally:
        server.stop()


def test_missing_supabase_url_fails_closed_with_a_clear_log_line(
    fresh_settings: pytest.MonkeyPatch,
    private_key: ec.EllipticCurvePrivateKey,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = _add_probe_route(create_app())

    with caplog.at_level(logging.ERROR):
        response = TestClient(app).get("/_probe", headers=_bearer(make_token(private_key)))

    assert response.status_code == 503
    assert any("SUPABASE_URL is not configured" in r.getMessage() for r in caplog.records)
