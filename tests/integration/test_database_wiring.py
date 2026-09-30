"""Pool lifecycle and the per-request connection dependency."""

import logging
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import DbConn, get_current_user
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import AuthUser
from app.main import create_app
from tests.integration.conftest import DATABASE_URL, requires_db


def _add_probe(app: FastAPI) -> None:
    @app.get("/_db")
    def db_probe(conn: DbConn) -> dict[str, int]:
        row = conn.execute("select 1 as one").fetchone()
        assert row is not None
        return {"one": row["one"]}

    app.dependency_overrides[get_current_user] = lambda: AuthUser(id=uuid.uuid4())


@pytest.fixture(autouse=True)
def fresh_settings() -> None:
    get_settings.cache_clear()
    yield  # type: ignore[misc]
    get_settings.cache_clear()


def test_without_a_database_url_the_app_still_starts_and_db_routes_fail_closed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app = create_app()
    _add_probe(app)

    with TestClient(app) as client, caplog.at_level(logging.ERROR):
        assert client.get("/health").status_code == 200
        response = client.get("/_db")

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "database_unavailable",
            "message": "The database is temporarily unavailable.",
        }
    }
    assert any("DATABASE_URL is not configured" in r.getMessage() for r in caplog.records)


def test_an_unreachable_database_times_out_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db?connect_timeout=1")
    monkeypatch.setenv("DB_POOL_TIMEOUT_SECONDS", "0.5")
    app = create_app()
    _add_probe(app)

    with TestClient(app) as client, caplog.at_level(logging.ERROR):
        assert client.get("/health").status_code == 200  # liveness does not need the DB
        response = client.get("/_db")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"
    assert any("database connection" in r.getMessage().lower() for r in caplog.records)


@requires_db
@pytest.mark.local_supabase
def test_a_real_pool_serves_requests_and_closes_on_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert DATABASE_URL
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    app = create_app()
    _add_probe(app)

    with TestClient(app) as client:
        assert client.get("/_db").json() == {"one": 1}
        pool = app.state.pool
        assert not pool.closed

    assert pool.closed


# ---- transaction boundary: commit/rollback must happen BEFORE the response is sent ----


class _FakeConnection:
    pass


class _RecordingPool:
    """Stands in for ConnectionPool; records when the pooled connection context exits."""

    def __init__(self, events: list[str]) -> None:
        self._events = events

    def connection(self) -> "_RecordingPool._Context":
        return _RecordingPool._Context(self._events)

    class _Context:
        def __init__(self, events: list[str]) -> None:
            self._events = events

        def __enter__(self) -> _FakeConnection:
            return _FakeConnection()

        def __exit__(self, exc_type: object, *_: object) -> None:
            self._events.append("rollback" if exc_type else "commit")


def _spy_on_response_start(app: FastAPI, events: list[str]) -> Any:
    async def asgi(scope: Any, receive: Any, send: Any) -> None:
        async def spy_send(message: Any) -> None:
            if message["type"] == "http.response.start":
                events.append("response_start")
            await send(message)

        await app(scope, receive, spy_send)

    return asgi


def _app_with_recording_pool(events: list[str]) -> tuple[FastAPI, TestClient]:
    app = create_app()

    @app.get("/_ok")
    def ok(_: DbConn) -> dict[str, bool]:
        events.append("handler")
        return {"ok": True}

    @app.get("/_fail")
    def fail(_: DbConn) -> None:
        events.append("handler")
        raise AppError("boom", "boom", 409)

    app.dependency_overrides[get_current_user] = lambda: AuthUser(id=uuid.uuid4())
    client = TestClient(_spy_on_response_start(app, events))
    return app, client


def test_the_transaction_commits_before_the_response_is_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    events: list[str] = []
    app, client = _app_with_recording_pool(events)

    with client:
        app.state.pool = _RecordingPool(events)
        assert client.get("/_ok").status_code == 200

    assert events == ["handler", "commit", "response_start"]


def test_the_transaction_rolls_back_before_an_error_response_is_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    events: list[str] = []
    app, client = _app_with_recording_pool(events)

    with client:
        app.state.pool = _RecordingPool(events)
        assert client.get("/_fail").status_code == 409

    assert events == ["handler", "rollback", "response_start"]
