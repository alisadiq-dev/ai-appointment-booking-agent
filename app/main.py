import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routers import admin, bookings, catalog, health
from app.core.config import get_settings
from app.core.db import create_pool
from app.core.errors import register_exception_handlers

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    pool = None
    if settings.database_url:
        pool = create_pool(settings)
        pool.open(wait=False)  # start serving (e.g. /health) even if the DB is briefly down
    else:
        logger.warning("DATABASE_URL is not configured; database-backed endpoints return 503")
    app.state.pool = pool
    try:
        yield
    finally:
        if pool is not None:
            pool.close()


def create_app() -> FastAPI:
    app = FastAPI(title="AI Appointment Booking Agent", lifespan=lifespan)
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(catalog.router)
    app.include_router(bookings.router)
    app.include_router(admin.router)
    return app


app = create_app()
