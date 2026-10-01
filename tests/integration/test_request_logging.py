"""Request id, access log line, and proof that secrets never reach the logs."""

import io
import json
import logging
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.agents.model import Interpretation
from app.api.deps import get_chat_agent, get_current_user
from app.core.logging import JsonFormatter
from app.core.security import AuthUser
from app.main import create_app
from tests.agent_helpers import ALICE, AgentHarness


@pytest.fixture
def log_stream() -> Iterator[io.StringIO]:
    """Everything logged during the test, in the production JSON format, at DEBUG."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield stream
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)


def _lines(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def _output(stream: io.StringIO) -> str:
    """The app's own log output. The HTTP test client logs the URLs it requests, which is the
    caller's side of the conversation and not something the server logged."""
    return "\n".join(
        json.dumps(line) for line in _lines(stream) if not line["logger"].startswith("httpx")
    )


def _access(stream: io.StringIO) -> list[dict[str, Any]]:
    return [line for line in _lines(stream) if line["logger"] == "app.access"]


# ------------------------------------------------------------------ request id


def test_every_response_has_a_request_id() -> None:
    response = TestClient(create_app()).get("/health")

    assert uuid.UUID(response.headers["x-request-id"])  # generated ids are uuid4 values


def test_a_valid_inbound_request_id_is_reused() -> None:
    response = TestClient(create_app()).get("/health", headers={"X-Request-ID": "trace-1234.abc_9"})

    assert response.headers["x-request-id"] == "trace-1234.abc_9"


@pytest.mark.parametrize(
    "inbound",
    [
        "short",
        "x" * 65,
        "has space in it",
        "inject\\nline",
        "<script>alert(1)</script>",
        "bad/slash",
    ],
)
def test_an_unsafe_inbound_request_id_is_replaced(inbound: str) -> None:
    response = TestClient(create_app()).get("/health", headers={"X-Request-ID": inbound})

    assert response.headers["x-request-id"] != inbound
    assert uuid.UUID(response.headers["x-request-id"])


def test_request_ids_differ_between_requests() -> None:
    client = TestClient(create_app())

    ids = {client.get("/health").headers["x-request-id"] for _ in range(5)}

    assert len(ids) == 5


def test_error_responses_carry_the_request_id_too() -> None:
    client = TestClient(create_app(), raise_server_exceptions=False)

    assert client.get("/nope").headers["x-request-id"]
    assert client.get("/bookings").headers["x-request-id"]  # 401


def test_unhandled_error_response_and_log_share_the_request_id(log_stream: io.StringIO) -> None:
    app = create_app()

    @app.get("/_boom")
    def boom() -> None:
        raise RuntimeError("kaboom")

    response = TestClient(app, raise_server_exceptions=False).get("/_boom")

    rid = response.headers["x-request-id"]
    error_lines = [line for line in _lines(log_stream) if line["level"] == "ERROR"]
    assert error_lines
    assert all(line["request_id"] == rid for line in error_lines)
    assert any(line["status"] == 500 for line in _access(log_stream))


# ------------------------------------------------------------------ access log


def test_one_access_line_per_request_with_route_status_and_duration(
    log_stream: io.StringIO,
) -> None:
    response = TestClient(create_app()).get("/health")

    [line] = _access(log_stream)
    assert line["request_id"] == response.headers["x-request-id"]
    assert (line["method"], line["route"], line["status"]) == ("GET", "/health", 200)
    assert line["duration_ms"] >= 0


def test_access_line_uses_the_route_template_not_the_raw_path(log_stream: io.StringIO) -> None:
    booking_id = uuid.uuid4()

    TestClient(create_app()).patch(f"/bookings/{booking_id}", json={})

    [line] = _access(log_stream)
    assert line["route"] == "/bookings/{booking_id}"
    assert str(booking_id) not in _output(log_stream)


def test_unknown_paths_and_query_strings_are_not_logged(log_stream: io.StringIO) -> None:
    TestClient(create_app()).get("/attacker-chosen-path?token=abc123secret")

    [line] = _access(log_stream)
    assert line["status"] == 404
    assert "attacker-chosen-path" not in _output(log_stream)
    assert "abc123secret" not in _output(log_stream)


def test_other_log_lines_in_a_request_carry_its_request_id(log_stream: io.StringIO) -> None:
    app = create_app()

    @app.get("/_note")
    def note() -> dict[str, str]:
        logging.getLogger("app.something").warning("inside the request")
        return {}

    response = TestClient(app).get("/_note")

    inner = next(line for line in _lines(log_stream) if line["message"] == "inside the request")
    assert inner["request_id"] == response.headers["x-request-id"]


# ------------------------------------------------------------------ secrets never logged

CANARY_HEADER_VALUE = "eyJhbGciOiJFUzI1NiJ9.SECRET-PAYLOAD-xyz.SECRET-SIGNATURE-xyz"
CANARY_CHAT_TEXT = "my-private-booking-note-9f8e7d"


def _chat_client() -> TestClient:
    harness = AgentHarness(*[Interpretation(action="other")] * 10)
    app = create_app()

    def fake_user(_: Request) -> AuthUser:
        return AuthUser(id=ALICE)

    app.dependency_overrides[get_current_user] = fake_user
    app.dependency_overrides[get_chat_agent] = lambda: harness.agent
    return TestClient(app, raise_server_exceptions=False)


def test_authorization_header_never_appears_in_the_logs_with_a_rejected_token(
    log_stream: io.StringIO,
) -> None:
    # No overrides: the real verifier rejects this token (and logs the rejection reason).
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get("/bookings", headers={"Authorization": f"Bearer {CANARY_HEADER_VALUE}"})

    assert response.status_code in (401, 503)
    output = _output(log_stream)
    assert output  # something was logged, so the assertions below are not vacuous
    assert CANARY_HEADER_VALUE not in output
    assert "SECRET-PAYLOAD" not in output
    assert "SECRET-SIGNATURE" not in output
    assert "Bearer" not in output


def test_authorization_header_never_appears_in_the_logs_on_a_successful_request(
    log_stream: io.StringIO,
) -> None:
    response = _chat_client().post(
        "/chat",
        json={"message": "hello"},
        headers={"Authorization": f"Bearer {CANARY_HEADER_VALUE}"},
    )

    assert response.status_code == 200
    assert _output(log_stream)
    assert "SECRET-PAYLOAD" not in _output(log_stream)
    assert "Bearer" not in _output(log_stream)


def test_chat_message_text_never_appears_in_the_logs(log_stream: io.StringIO) -> None:
    response = _chat_client().post("/chat", json={"message": CANARY_CHAT_TEXT})

    assert response.status_code == 200
    assert _access(log_stream)  # the request was logged...
    assert CANARY_CHAT_TEXT not in _output(log_stream)  # ...without the text


def test_invalid_chat_body_is_not_logged_either(log_stream: io.StringIO) -> None:
    response = _chat_client().post(
        "/chat", json={"message": CANARY_CHAT_TEXT, "extra": CANARY_CHAT_TEXT}
    )

    assert response.status_code == 422
    assert CANARY_CHAT_TEXT not in _output(log_stream)
    assert CANARY_CHAT_TEXT not in response.text


def test_a_failing_chat_turn_does_not_log_the_message(log_stream: io.StringIO) -> None:
    app = create_app()

    def fake_user(_: Request) -> AuthUser:
        return AuthUser(id=ALICE)

    def exploding_agent() -> Any:
        raise RuntimeError("agent exploded")

    app.dependency_overrides[get_current_user] = fake_user
    app.dependency_overrides[get_chat_agent] = exploding_agent

    response = TestClient(app, raise_server_exceptions=False).post(
        "/chat", json={"message": CANARY_CHAT_TEXT}
    )

    assert response.status_code == 500
    assert any(line["level"] == "ERROR" for line in _lines(log_stream))
    assert CANARY_CHAT_TEXT not in _output(log_stream)
