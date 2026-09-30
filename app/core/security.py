import logging
from typing import Protocol
from uuid import UUID

import jwt
from cryptography.hazmat.primitives.asymmetric.types import PublicKeyTypes
from jwt import PyJWK
from pydantic import BaseModel, ConfigDict

from app.core.errors import AppError

logger = logging.getLogger(__name__)

# Asymmetric algorithms only. HS256 is deliberately absent so a token can never be
# "verified" using a public key as an HMAC secret.
ALLOWED_ALGORITHMS = ["ES256", "RS256"]
REQUIRED_CLAIMS = ["exp", "iss", "aud", "sub"]
CLOCK_SKEW_LEEWAY_SECONDS = 10


class UnauthorizedError(AppError):
    """Missing or invalid credentials. The message is deliberately generic."""

    def __init__(self) -> None:
        super().__init__(
            code="unauthorized",
            message="Invalid or missing authentication token.",
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )


class AuthUnavailableError(AppError):
    """Tokens cannot be verified right now (e.g. JWKS endpoint down). Fails closed."""

    def __init__(self) -> None:
        super().__init__(
            code="auth_unavailable",
            message="Authentication is temporarily unavailable.",
            status_code=503,
        )


class AuthUser(BaseModel):
    """The authenticated caller. Identity only: authorization never uses token claims."""

    model_config = ConfigDict(frozen=True)

    id: UUID


SigningKey = PyJWK | PublicKeyTypes


class SigningKeyProvider(Protocol):
    def get_signing_key(self, token: str) -> SigningKey:
        """Return the public key that should verify `token`.

        Raises UnauthorizedError if no matching key exists, AuthUnavailableError if the
        key source cannot be reached.
        """
        ...


class TokenVerifier:
    def __init__(self, key_provider: SigningKeyProvider, issuer: str, audience: str) -> None:
        self._key_provider = key_provider
        self._issuer = issuer
        self._audience = audience

    def verify(self, token: str) -> AuthUser:
        try:
            key = self._key_provider.get_signing_key(token)
            claims = jwt.decode(
                token,
                key,
                algorithms=ALLOWED_ALGORITHMS,
                issuer=self._issuer,
                audience=self._audience,
                leeway=CLOCK_SKEW_LEEWAY_SECONDS,
                options={"require": REQUIRED_CLAIMS},
            )
            return AuthUser(id=UUID(claims["sub"]))
        except (jwt.PyJWTError, ValueError, TypeError, AttributeError) as exc:
            # Log the reason for operators; never tell the caller why.
            logger.info("token rejected: %s", type(exc).__name__)
            raise UnauthorizedError from exc
