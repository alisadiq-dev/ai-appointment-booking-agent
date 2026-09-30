import json
import logging

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError, PyJWKSetError

from app.core.security import AuthUnavailableError, SigningKey, UnauthorizedError

logger = logging.getLogger(__name__)


class JwksKeyProvider:
    """Looks up token signing keys in Supabase's JWKS endpoint.

    Keys are cached for `cache_seconds`, every fetch has a timeout, and the JWKS URL comes
    only from configuration (never from the token). PyJWT limits refetches triggered by
    unknown key ids to one per cooldown window, so forged `kid` values cannot hammer the
    endpoint. If the endpoint cannot be reached or returns bad data, verification fails closed.
    """

    def __init__(self, jwks_url: str, cache_seconds: float, timeout_seconds: float) -> None:
        self._jwks_url = jwks_url
        self._client = PyJWKClient(
            jwks_url,
            cache_jwk_set=True,
            lifespan=cache_seconds,
            timeout=timeout_seconds,
        )

    def get_signing_key(self, token: str) -> SigningKey:
        try:
            return self._client.get_signing_key_from_jwt(token)
        except PyJWKClientConnectionError as exc:
            logger.error(
                "JWKS endpoint unreachable, rejecting tokens (fail closed): %s: %s",
                self._jwks_url,
                exc,
            )
            raise AuthUnavailableError from exc
        except (PyJWKSetError, json.JSONDecodeError) as exc:
            logger.error(
                "JWKS endpoint returned an invalid key set, rejecting tokens (fail closed): %s: %s",
                self._jwks_url,
                exc,
            )
            raise AuthUnavailableError from exc
        except (PyJWKClientError, jwt.DecodeError) as exc:
            logger.info("no signing key for token: %s", type(exc).__name__)
            raise UnauthorizedError from exc
