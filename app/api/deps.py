import logging
from collections.abc import Iterator
from contextlib import ExitStack
from datetime import timedelta
from functools import lru_cache
from typing import Annotated

import psycopg
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg_pool import PoolTimeout

from app.core.config import Settings, get_settings
from app.core.errors import DatabaseUnavailableError
from app.core.security import AuthUnavailableError, AuthUser, TokenVerifier, UnauthorizedError
from app.integrations.supabase_jwks import JwksKeyProvider
from app.repositories.bookings import BookingRepository
from app.repositories.business_hours import BusinessHoursRepository
from app.repositories.profiles import ProfileRepository
from app.repositories.services import ServiceRepository
from app.repositories.types import Conn
from app.services.booking_service import BookingService

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


# Sync generator dependency: one pooled connection per request. The pool's connection
# context commits when the request succeeds and rolls back when it raises.
# Used with scope="function" (below) so that happens BEFORE the response is sent: FastAPI's
# default for yield dependencies is to clean up after the response, which would let a client
# see "201 Created" before the booking is committed.
def get_db_connection(request: Request) -> Iterator[Conn]:
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        logger.error("DATABASE_URL is not configured, database requests fail closed")
        raise DatabaseUnavailableError
    with ExitStack() as stack:
        try:
            conn = stack.enter_context(pool.connection())
        except (PoolTimeout, psycopg.OperationalError) as exc:
            logger.error("Could not get a database connection from the pool: %s", exc)
            raise DatabaseUnavailableError from exc
        yield conn


DbConn = Annotated[Conn, Depends(get_db_connection, scope="function")]


def get_booking_service(
    conn: DbConn, settings: Annotated[Settings, Depends(get_settings)]
) -> BookingService:
    return BookingService(
        bookings=BookingRepository(conn),
        services=ServiceRepository(conn),
        business_hours=BusinessHoursRepository(conn),
        profiles=ProfileRepository(conn),
        zone=settings.business_zone,
        slot_interval=timedelta(minutes=settings.slot_interval_minutes),
    )


BookingServiceDep = Annotated[BookingService, Depends(get_booking_service)]
