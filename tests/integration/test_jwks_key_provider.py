import logging
import socket
import time
from collections.abc import Iterator

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.core.security import AuthUnavailableError, TokenVerifier, UnauthorizedError
from app.integrations.supabase_jwks import JwksKeyProvider
from tests.auth_helpers import (
    AUDIENCE,
    ISSUER,
    KID,
    USER_ID,
    generate_es256_key,
    jwks_document,
    make_token,
)
from tests.fake_jwks_server import FakeJwksServer


@pytest.fixture(scope="module")
def private_key() -> ec.EllipticCurvePrivateKey:
    return generate_es256_key()


@pytest.fixture
def server(private_key: ec.EllipticCurvePrivateKey) -> Iterator[FakeJwksServer]:
    fake = FakeJwksServer(jwks_document((KID, private_key)))
    fake.start()
    yield fake
    fake.stop()


def _provider(url: str, cache_seconds: float = 300, timeout: float = 2) -> JwksKeyProvider:
    return JwksKeyProvider(url, cache_seconds=cache_seconds, timeout_seconds=timeout)


def test_resolves_the_signing_key_and_a_valid_token_verifies(
    server: FakeJwksServer, private_key: ec.EllipticCurvePrivateKey
) -> None:
    verifier = TokenVerifier(_provider(server.url), issuer=ISSUER, audience=AUDIENCE)

    user = verifier.verify(make_token(private_key))

    assert str(user.id) == USER_ID


def test_jwks_is_cached_between_requests(
    server: FakeJwksServer, private_key: ec.EllipticCurvePrivateKey
) -> None:
    provider = _provider(server.url)
    token = make_token(private_key)

    provider.get_signing_key(token)
    provider.get_signing_key(token)
    provider.get_signing_key(token)

    assert server.hits == 1


def test_jwks_is_refetched_after_the_cache_expires(
    server: FakeJwksServer, private_key: ec.EllipticCurvePrivateKey
) -> None:
    provider = _provider(server.url, cache_seconds=0.1)
    token = make_token(private_key)

    provider.get_signing_key(token)
    time.sleep(0.25)
    provider.get_signing_key(token)

    assert server.hits == 2


def test_unknown_kid_is_unauthorized_and_cannot_hammer_the_endpoint(
    server: FakeJwksServer,
) -> None:
    provider = _provider(server.url)
    attacker_key = generate_es256_key()

    for i in range(10):
        with pytest.raises(UnauthorizedError):
            provider.get_signing_key(make_token(attacker_key, kid=f"random-{i}"))

    assert server.hits <= 2  # initial fetch + at most one refresh (PyJWT cooldown)


def test_token_without_kid_is_unauthorized(server: FakeJwksServer) -> None:
    provider = _provider(server.url)

    with pytest.raises(UnauthorizedError):
        provider.get_signing_key(make_token(generate_es256_key(), kid=None))


def test_garbage_token_is_unauthorized(server: FakeJwksServer) -> None:
    with pytest.raises(UnauthorizedError):
        _provider(server.url).get_signing_key("not-a-jwt")


def test_unreachable_endpoint_fails_closed_with_a_clear_log_line(
    private_key: ec.EllipticCurvePrivateKey, caplog: pytest.LogCaptureFixture
) -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed_port = s.getsockname()[1]
    url = f"http://127.0.0.1:{closed_port}/auth/v1/.well-known/jwks.json"
    provider = _provider(url)

    with caplog.at_level(logging.ERROR), pytest.raises(AuthUnavailableError):
        provider.get_signing_key(make_token(private_key))

    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any("JWKS endpoint unreachable" in m and url in m for m in messages)


def test_slow_endpoint_times_out_and_fails_closed(
    server: FakeJwksServer, private_key: ec.EllipticCurvePrivateKey
) -> None:
    server.mode = "slow"
    provider = _provider(server.url, timeout=0.2)

    started = time.monotonic()
    with pytest.raises(AuthUnavailableError):
        provider.get_signing_key(make_token(private_key))

    assert time.monotonic() - started < 0.8


def test_server_error_fails_closed(
    server: FakeJwksServer,
    private_key: ec.EllipticCurvePrivateKey,
    caplog: pytest.LogCaptureFixture,
) -> None:
    server.mode = "error"

    with caplog.at_level(logging.ERROR), pytest.raises(AuthUnavailableError):
        _provider(server.url).get_signing_key(make_token(private_key))

    assert any("JWKS endpoint" in r.getMessage() for r in caplog.records)


def test_invalid_jwks_body_fails_closed(
    server: FakeJwksServer, private_key: ec.EllipticCurvePrivateKey
) -> None:
    server.mode = "garbage"

    with pytest.raises(AuthUnavailableError):
        _provider(server.url).get_signing_key(make_token(private_key))


def test_a_failed_refresh_does_not_wipe_the_cached_keys(
    server: FakeJwksServer, private_key: ec.EllipticCurvePrivateKey
) -> None:
    provider = _provider(server.url, cache_seconds=0.1)
    token = make_token(private_key)
    provider.get_signing_key(token)
    time.sleep(0.25)  # cache expired; endpoint now failing

    server.mode = "error"
    with pytest.raises(AuthUnavailableError):
        provider.get_signing_key(token)

    server.mode = "ok"
    assert provider.get_signing_key(token) is not None  # recovers as soon as the endpoint does
