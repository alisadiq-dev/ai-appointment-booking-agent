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

from app.agents.gemini import build_language_model
from app.agents.graph import BookingAgent
from app.agents.model import LanguageModel
from app.core.config import Settings, get_settings
from app.core.errors import DatabaseUnavailableError
from app.core.rate_limit import SlidingWindowRateLimiter
from app.core.security import AuthUnavailableError, AuthUser, TokenVerifier, UnauthorizedError
from app.integrations.google_calendar import GoogleCalendar
from app.integrations.null_calendar import NullCalendar
from app.integrations.supabase_jwks import JwksKeyProvider
from app.repositories.bookings import BookingRepository
from app.repositories.business_hours import BusinessHoursRepository
from app.repositories.profiles import ProfileRepository
from app.repositories.services import ServiceRepository
from app.repositories.sessions import SessionRepository
from app.repositories.types import Conn
from app.services.booking_service import BookingService
from app.services.errors import TurnInProgressError
from app.services.ports import CalendarPort

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


@lru_cache
def get_calendar() -> CalendarPort:
    """One shared calendar client (it holds the credentials and refreshes tokens)."""
    settings = get_settings()
    if not settings.calendar_enabled:
        return NullCalendar()
    return GoogleCalendar.from_settings(settings)


def get_booking_service(
    conn: DbConn,
    settings: Annotated[Settings, Depends(get_settings)],
    calendar: Annotated[CalendarPort, Depends(get_calendar)],
) -> BookingService:
    return BookingService(
        bookings=BookingRepository(conn),
        services=ServiceRepository(conn),
        business_hours=BusinessHoursRepository(conn),
        profiles=ProfileRepository(conn),
        calendar=calendar,
        zone=settings.business_zone,
        slot_interval=timedelta(minutes=settings.slot_interval_minutes),
    )


BookingServiceDep = Annotated[BookingService, Depends(get_booking_service)]


# ------------------------------------------------------------------ chat


@lru_cache
def get_chat_rate_limiter() -> SlidingWindowRateLimiter:
    """One limiter for the whole process. In memory, so the limit is per instance."""
    settings = get_settings()
    return SlidingWindowRateLimiter(
        limit=settings.chat_rate_limit_requests,
        window_seconds=settings.chat_rate_limit_window_seconds,
    )


def rate_limited_user(
    user: CurrentUser,
    limiter: Annotated[SlidingWindowRateLimiter, Depends(get_chat_rate_limiter)],
) -> AuthUser:
    """The authenticated user, after their chat budget is charged. Keyed by the JWT user id (never
    the IP), and only reached once the token is valid, so anonymous traffic cannot spend a user's
    budget. Runs before the database connection and the model call are set up."""
    limiter.check(str(user.id))
    return user


ChatUser = Annotated[AuthUser, Depends(rate_limited_user)]


@lru_cache
def get_language_model() -> LanguageModel:
    return build_language_model(get_settings())


def get_chat_agent(
    user: ChatUser,
    conn: DbConn,
    service: BookingServiceDep,
    model: Annotated[LanguageModel, Depends(get_language_model)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> BookingAgent:
    """The agent for one chat turn, on this request's single database transaction.

    Everything the turn writes (bookings and the saved session) shares that transaction, which
    commits only if the turn succeeds. The per-user lock fails fast instead of waiting, so a second
    simultaneous message from the same user never holds another pooled connection.
    """
    sessions = SessionRepository(conn)
    if not sessions.try_lock_turn(user.id):
        raise TurnInProgressError
    return BookingAgent(
        bookings=service, model=model, sessions=sessions, zone=settings.business_zone
    )


ChatAgentDep = Annotated[BookingAgent, Depends(get_chat_agent)]
