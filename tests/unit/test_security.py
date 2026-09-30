import time
from typing import Any
from uuid import UUID

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.core.security import (
    AuthUnavailableError,
    AuthUser,
    TokenVerifier,
    UnauthorizedError,
)
from tests.auth_helpers import (
    AUDIENCE,
    ISSUER,
    USER_ID,
    StaticKeyProvider,
    forge_hs256_with_public_key,
    forge_unsigned_token,
    generate_es256_key,
    make_token,
)


@pytest.fixture(scope="module")
def private_key() -> ec.EllipticCurvePrivateKey:
    return generate_es256_key()


@pytest.fixture
def verifier(private_key: ec.EllipticCurvePrivateKey) -> TokenVerifier:
    provider = StaticKeyProvider(key=private_key.public_key())
    return TokenVerifier(provider, issuer=ISSUER, audience=AUDIENCE)


def test_valid_token_returns_the_user_id(
    verifier: TokenVerifier, private_key: ec.EllipticCurvePrivateKey
) -> None:
    user = verifier.verify(make_token(private_key))

    assert user == AuthUser(id=UUID(USER_ID))


def test_auth_user_carries_no_authorization_claims() -> None:
    # Authorization must never come from the token's role claim.
    assert set(AuthUser.model_fields) == {"id"}


def test_token_role_claim_is_ignored(
    verifier: TokenVerifier, private_key: ec.EllipticCurvePrivateKey
) -> None:
    user = verifier.verify(make_token(private_key, role="service_role"))

    assert user == AuthUser(id=UUID(USER_ID))
    assert not hasattr(user, "role")


def test_expired_token_is_rejected(
    verifier: TokenVerifier, private_key: ec.EllipticCurvePrivateKey
) -> None:
    token = make_token(private_key, exp=int(time.time()) - 3600)

    with pytest.raises(UnauthorizedError):
        verifier.verify(token)


def test_token_without_exp_is_rejected(
    verifier: TokenVerifier, private_key: ec.EllipticCurvePrivateKey
) -> None:
    with pytest.raises(UnauthorizedError):
        verifier.verify(make_token(private_key, omit=("exp",)))


def test_token_not_yet_valid_is_rejected(
    verifier: TokenVerifier, private_key: ec.EllipticCurvePrivateKey
) -> None:
    with pytest.raises(UnauthorizedError):
        verifier.verify(make_token(private_key, nbf=int(time.time()) + 3600))


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "https://evil.example/auth/v1"},
        {"aud": "some-other-audience"},
    ],
)
def test_wrong_issuer_or_audience_is_rejected(
    verifier: TokenVerifier,
    private_key: ec.EllipticCurvePrivateKey,
    overrides: dict[str, Any],
) -> None:
    with pytest.raises(UnauthorizedError):
        verifier.verify(make_token(private_key, **overrides))


@pytest.mark.parametrize("omitted", ["iss", "aud", "sub"])
def test_token_missing_required_claim_is_rejected(
    verifier: TokenVerifier, private_key: ec.EllipticCurvePrivateKey, omitted: str
) -> None:
    with pytest.raises(UnauthorizedError):
        verifier.verify(make_token(private_key, omit=(omitted,)))


@pytest.mark.parametrize("sub", ["not-a-uuid", "", 12345])
def test_sub_must_be_a_uuid(
    verifier: TokenVerifier, private_key: ec.EllipticCurvePrivateKey, sub: Any
) -> None:
    with pytest.raises(UnauthorizedError):
        verifier.verify(make_token(private_key, sub=sub))


def test_token_signed_by_a_different_key_is_rejected(verifier: TokenVerifier) -> None:
    attacker_key = generate_es256_key()

    with pytest.raises(UnauthorizedError):
        verifier.verify(make_token(attacker_key))


def test_alg_none_token_is_rejected(verifier: TokenVerifier) -> None:
    with pytest.raises(UnauthorizedError):
        verifier.verify(forge_unsigned_token())


def test_hs256_signed_with_the_public_key_is_rejected(
    verifier: TokenVerifier, private_key: ec.EllipticCurvePrivateKey
) -> None:
    with pytest.raises(UnauthorizedError):
        verifier.verify(forge_hs256_with_public_key(private_key.public_key()))


@pytest.mark.parametrize("token", ["", "garbage", "a.b.c", "....", "Bearer x"])
def test_malformed_token_is_rejected(verifier: TokenVerifier, token: str) -> None:
    with pytest.raises(UnauthorizedError):
        verifier.verify(token)


def test_signing_key_lookup_failure_is_unauthorized(
    private_key: ec.EllipticCurvePrivateKey,
) -> None:
    provider = StaticKeyProvider(error=UnauthorizedError())
    verifier = TokenVerifier(provider, issuer=ISSUER, audience=AUDIENCE)

    with pytest.raises(UnauthorizedError):
        verifier.verify(make_token(private_key))


def test_unavailable_key_source_is_reported_as_unavailable_not_unauthorized(
    private_key: ec.EllipticCurvePrivateKey,
) -> None:
    provider = StaticKeyProvider(error=AuthUnavailableError())
    verifier = TokenVerifier(provider, issuer=ISSUER, audience=AUDIENCE)

    with pytest.raises(AuthUnavailableError):
        verifier.verify(make_token(private_key))


def test_auth_errors_use_generic_messages_and_status_codes() -> None:
    unauthorized = UnauthorizedError()
    unavailable = AuthUnavailableError()

    assert (unauthorized.status_code, unauthorized.code) == (401, "unauthorized")
    assert unauthorized.headers == {"WWW-Authenticate": "Bearer"}
    assert (unavailable.status_code, unavailable.code) == (503, "auth_unavailable")
