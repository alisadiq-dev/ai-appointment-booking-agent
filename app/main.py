import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.deps import get_calendar, get_language_model
from app.api.routers import admin, bookings, catalog, chat, health
from app.core.config import get_settings
from app.core.db import create_pool
from app.core.errors import register_exception_handlers
from app.core.hardening import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from app.core.logging import configure_logging
from app.core.request_context import RequestContextMiddleware

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    pool = None
    if settings.database_url:
        pool = create_pool(settings)
        pool.open(wait=False)  # start serving (e.g. /health) even if the DB is briefly down
    else:
        logger.warning("DATABASE_URL is not configured; database-backed endpoints return 503")
    app.state.pool = pool
    if settings.calendar_enabled:
        get_calendar()  # builds the Google client now, so a bad key stops startup (fail fast)
    else:
        logger.warning(
            "CALENDAR_ENABLED is false: bookings are NOT synced to Google Calendar "
            "(must be true in production)"
        )
    get_language_model()  # logs a warning now if GEMINI_API_KEY is missing
    try:
        yield
    finally:
        if pool is not None:
            pool.close()


def create_app() -> FastAPI:
    app = FastAPI(title="AI Appointment Booking Agent", lifespan=lifespan)
    register_exception_handlers(app)
    # The last one added is the outermost: the request id and access log wrap everything else.
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, hsts=get_settings().app_env == "production")
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health.router)
    app.include_router(catalog.router)
    app.include_router(bookings.router)
    app.include_router(admin.router)
    app.include_router(chat.router)
    return app


app = create_app()
