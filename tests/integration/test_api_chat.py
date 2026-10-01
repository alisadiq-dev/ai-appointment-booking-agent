"""HTTP-level tests for POST /chat with in-memory fakes (no database, no Gemini)."""

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.agents.model import Interpretation
from app.api.deps import get_chat_agent, get_chat_rate_limiter, get_current_user
from app.core.rate_limit import SlidingWindowRateLimiter
from app.core.security import AuthUser
from app.main import create_app
from tests.agent_helpers import ALICE, BOB, AgentHarness


class Clock:
    now = 5000.0

    def __call__(self) -> float:
        return self.now


class Setup:
    def __init__(self, limit: int = 3) -> None:
        self.harness = AgentHarness(*[Interpretation(action="other")] * 50)
        self.clock = Clock()
        self.agent_calls: list[uuid.UUID] = []
        app = create_app()

        def fake_user(request: Request) -> AuthUser:
            return AuthUser(id=uuid.UUID(request.headers["x-test-user"]))

        def agent_factory(request: Request) -> Any:
            self.agent_calls.append(uuid.UUID(request.headers["x-test-user"]))
            return self.harness.agent

        app.dependency_overrides[get_current_user] = fake_user
        app.dependency_overrides[get_chat_agent] = agent_factory
        limiter = SlidingWindowRateLimiter(limit=limit, window_seconds=60, clock=self.clock)
        app.dependency_overrides[get_chat_rate_limiter] = lambda: limiter
        self.client = TestClient(app)

    def post(self, body: Any, user: uuid.UUID = ALICE) -> Any:
        return self.client.post("/chat", json=body, headers={"x-test-user": str(user)})


@pytest.fixture
def chat() -> Setup:
    return Setup()


# ------------------------------------------------------------------ happy path and auth


def test_a_message_gets_a_reply(chat: Setup) -> None:
    response = chat.post({"message": "hello"})

    assert response.status_code == 200
    assert set(response.json()) == {"reply"}
    assert "book" in response.json()["reply"].lower()


def test_the_agent_is_built_for_the_user_in_the_token(chat: Setup) -> None:
    chat.post({"message": "hello"}, user=BOB)

    assert chat.agent_calls == [BOB]


def test_no_token_is_401_in_the_standard_format() -> None:
    client = TestClient(create_app())

    response = client.post("/chat", json={"message": "hello"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


# ------------------------------------------------------------------ input validation


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"message": ""},
        {"message": "   \n\t "},
        {"message": "x" * 1001},
        {"message": 42},
        {"message": None},
        {"message": ["hi"]},
        {"message": "hi", "user_id": str(BOB)},
        {"message": "hi", "role": "admin"},
    ],
)
def test_invalid_bodies_are_422_and_never_reach_the_agent(chat: Setup, body: Any) -> None:
    response = chat.post(body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert chat.harness.model.calls == []


def test_a_message_of_exactly_the_maximum_length_is_accepted(chat: Setup) -> None:
    assert chat.post({"message": "x" * 1000}).status_code == 200


def test_surrounding_whitespace_is_trimmed_before_the_agent_sees_it(chat: Setup) -> None:
    chat.post({"message": "   hello there  \n"})

    assert chat.harness.model.calls[0][0] == "hello there"


def test_validation_errors_do_not_echo_the_message(chat: Setup) -> None:
    response = chat.post({"message": "secret-" + "x" * 1100})

    assert "secret-" not in response.text


def test_a_non_json_body_is_422(chat: Setup) -> None:
    response = chat.client.post(
        "/chat",
        content="not json",
        headers={"x-test-user": str(ALICE), "content-type": "text/plain"},
    )

    assert response.status_code == 422


# ------------------------------------------------------------------ rate limiting


def test_the_request_over_the_limit_is_429_with_retry_after(chat: Setup) -> None:
    for _ in range(3):
        assert chat.post({"message": "hello"}).status_code == 200

    response = chat.post({"message": "hello"})

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
    assert response.headers["Retry-After"] == "60"
    assert len(chat.agent_calls) == 3  # the limited request never built an agent (no DB, no model)


def test_the_limit_is_per_user_not_shared(chat: Setup) -> None:
    for _ in range(3):
        chat.post({"message": "hello"}, user=ALICE)
    assert chat.post({"message": "hello"}, user=ALICE).status_code == 429

    assert chat.post({"message": "hello"}, user=BOB).status_code == 200


def test_the_limit_is_keyed_by_user_id_not_by_ip(chat: Setup) -> None:
    # Every TestClient request comes from the same address; two users still get their own budgets.
    for _ in range(3):
        assert chat.post({"message": "hi"}, user=ALICE).status_code == 200
        assert chat.post({"message": "hi"}, user=BOB).status_code == 200


def test_the_budget_comes_back_after_the_window(chat: Setup) -> None:
    for _ in range(3):
        chat.post({"message": "hello"})
    assert chat.post({"message": "hello"}).status_code == 429

    chat.clock.now += 60

    assert chat.post({"message": "hello"}).status_code == 200


def test_unauthenticated_requests_do_not_use_up_anyones_budget(chat: Setup) -> None:
    bare = TestClient(chat.client.app)  # same app, but without the fake-user header
    chat.client.app.dependency_overrides.pop(get_current_user)  # type: ignore[attr-defined]
    for _ in range(10):
        assert bare.post("/chat", json={"message": "hi"}).status_code == 401

    chat.client.app.dependency_overrides[get_current_user] = lambda: AuthUser(id=ALICE)  # type: ignore[attr-defined]
    assert chat.post({"message": "hi"}).status_code == 200


def test_invalid_requests_still_count_towards_the_limit(chat: Setup) -> None:
    for _ in range(3):
        assert chat.post({"message": ""}).status_code == 422

    assert chat.post({"message": "hello"}).status_code == 429


def test_the_limit_is_checked_before_the_database_is_touched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Real get_chat_agent, no DATABASE_URL: allowed requests fail with 503 (no database), but a
    # request over the limit must be 429, proving the limiter runs before the connection.
    from app.core.config import get_settings

    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthUser(id=ALICE)
    limiter = SlidingWindowRateLimiter(limit=2, window_seconds=60, clock=Clock())
    app.dependency_overrides[get_chat_rate_limiter] = lambda: limiter
    client = TestClient(app)

    statuses: list[Callable[[], int]] = [
        lambda: client.post("/chat", json={"message": "hi"}).status_code for _ in range(3)
    ]
    try:
        assert [call() for call in statuses] == [503, 503, 429]
    finally:
        get_settings.cache_clear()
