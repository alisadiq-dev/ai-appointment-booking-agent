"""404, 405 and unhandled errors use the standard {"error": {"code", "message"}} body."""

import logging

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


def _assert_standard(body: dict, code: str) -> None:
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == code


def test_unknown_route_is_404_in_the_standard_format() -> None:
    response = TestClient(create_app()).get("/no-such-route")

    assert response.status_code == 404
    _assert_standard(response.json(), "not_found")


def test_wrong_method_is_405_in_the_standard_format_and_keeps_allow() -> None:
    response = TestClient(create_app()).post("/health")

    assert response.status_code == 405
    _assert_standard(response.json(), "method_not_allowed")
    assert "GET" in response.headers["allow"]


def test_404_does_not_echo_the_requested_path() -> None:
    response = TestClient(create_app()).get("/secret-looking-path-123")

    assert "secret-looking-path-123" not in response.text


def test_unhandled_exception_is_500_without_details_and_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app()

    @app.get("/_boom")
    def boom() -> None:
        raise RuntimeError("internal detail: password=hunter2")

    client = TestClient(app, raise_server_exceptions=False)

    with caplog.at_level(logging.ERROR):
        response = client.get("/_boom")

    assert response.status_code == 500
    _assert_standard(response.json(), "internal_error")
    assert "hunter2" not in response.text
    assert "RuntimeError" not in response.text
    assert any(r.exc_info for r in caplog.records)  # the stack trace is kept for operators
