from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.core.config import Settings


def create_pool(settings: Settings) -> ConnectionPool[Any]:
    """A not-yet-open connection pool. Call `.open()` (the app lifespan does).

    Rows come back as dicts. Server-side prepared statements are disabled so the pool also
    works behind a transaction-mode pooler such as Supavisor/pgbouncer.
    """
    assert settings.database_url  # noqa: S101 - callers check this first
    return ConnectionPool(
        settings.database_url,
        min_size=settings.db_pool_min_size,
        max_size=max(settings.db_pool_max_size, settings.db_pool_min_size),
        timeout=settings.db_pool_timeout_seconds,
        kwargs={"row_factory": dict_row, "prepare_threshold": None, "connect_timeout": 10},
        check=ConnectionPool.check_connection,
        open=False,
        name="app",
    )
