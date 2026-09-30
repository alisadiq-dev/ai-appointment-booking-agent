import logging
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings
from app.core.security import AuthUnavailableError, AuthUser, TokenVerifier, UnauthorizedError
from app.integrations.supabase_jwks import JwksKeyProvider

logger = logging.getLogger(__name__)

# auto_error=False so a missing or malformed header goes through our error format.
bearer_scheme = HTTPBearer(auto_error=False)


@lru_cache
def get_token_verifier() -> TokenVerifier:
    settings = get_settings()
    if settings.jwt_issuer is None or settings.jwks_url is None:
        logger.error(
            "SUPABASE_URL is not configured, rejecting all authenticated requests (fail closed)"
        )
        raise AuthUnavailableError
    key_provider = JwksKeyProvider(
        settings.jwks_url,
        cache_seconds=settings.jwks_cache_seconds,
        timeout_seconds=settings.jwks_timeout_seconds,
    )
    return TokenVerifier(
        key_provider, issuer=settings.jwt_issuer, audience=settings.supabase_jwt_audience
    )


def get_bearer_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> str:
    if credentials is None or not credentials.credentials:
        raise UnauthorizedError
    return credentials.credentials


# Sync (not async) on purpose: the JWKS fetch is blocking I/O, and FastAPI runs sync
# dependencies in a threadpool so it cannot stall the event loop.
# `token` is declared first so a missing header is a 401 even if auth is misconfigured.
def get_current_user(
    token: Annotated[str, Depends(get_bearer_token)],
    verifier: Annotated[TokenVerifier, Depends(get_token_verifier)],
) -> AuthUser:
    return verifier.verify(token)


CurrentUser = Annotated[AuthUser, Depends(get_current_user)]
