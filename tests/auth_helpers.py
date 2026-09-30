"""Helpers for building Supabase-style JWTs and JWKS documents in tests."""

import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

ISSUER = "http://127.0.0.1:54321/auth/v1"
AUDIENCE = "authenticated"
KID = "test-key-1"
USER_ID = "057fab27-4c69-4a5c-8dcf-69f496c6cc5a"


def generate_es256_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def default_claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": USER_ID,
        "role": "authenticated",
        "iat": now,
        "exp": now + 3600,
    }
    claims.update(overrides)
    return claims


def make_token(
    private_key: ec.EllipticCurvePrivateKey,
    *,
    kid: str | None = KID,
    omit: tuple[str, ...] = (),
    **claim_overrides: Any,
) -> str:
    claims = {k: v for k, v in default_claims(**claim_overrides).items() if k not in omit}
    headers = {"kid": kid} if kid is not None else {}
    return jwt.encode(claims, private_key, algorithm="ES256", headers=headers)


def jwks_document(*keys: tuple[str, ec.EllipticCurvePrivateKey]) -> dict[str, Any]:
    """A JWKS document (public keys only) for the given (kid, private key) pairs."""
    jwks_keys = []
    for kid, private_key in keys:
        jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(private_key.public_key()))
        jwks_keys.append({**jwk, "kid": kid, "alg": "ES256", "use": "sig"})
    return {"keys": jwks_keys}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def forge_unsigned_token(**claim_overrides: Any) -> str:
    """A token with alg=none and no signature."""
    header = _b64url(json.dumps({"alg": "none", "typ": "JWT", "kid": KID}).encode())
    payload = _b64url(json.dumps(default_claims(**claim_overrides)).encode())
    return f"{header}.{payload}."


def forge_hs256_with_public_key(public_key: ec.EllipticCurvePublicKey) -> str:
    """Classic algorithm-confusion attack: HS256 signed using the public key bytes as the secret."""
    secret = public_key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT", "kid": KID}).encode())
    payload = _b64url(json.dumps(default_claims()).encode())
    signing_input = f"{header}.{payload}".encode()
    signature = _b64url(hmac.new(secret, signing_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


def new_user_id() -> str:
    return str(uuid.uuid4())


class StaticKeyProvider:
    """Key provider test double that always returns one public key, or raises."""

    def __init__(self, key: Any = None, error: Exception | None = None) -> None:
        self._key = key
        self._error = error

    def get_signing_key(self, token: str) -> Any:
        if self._error is not None:
            raise self._error
        return self._key
