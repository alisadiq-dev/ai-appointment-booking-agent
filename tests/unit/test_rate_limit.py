import contextlib
import threading
import uuid

import pytest

from app.core.errors import AppError
from app.core.rate_limit import RateLimitedError, SlidingWindowRateLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def limiter(clock: Clock, limit: int = 3, window: float = 60) -> SlidingWindowRateLimiter:
    return SlidingWindowRateLimiter(limit=limit, window_seconds=window, clock=clock)


def test_requests_up_to_the_limit_are_allowed() -> None:
    clock = Clock()
    rl = limiter(clock)

    for _ in range(3):
        rl.check("alice")  # does not raise


def test_the_request_over_the_limit_is_rejected_with_retry_after() -> None:
    clock = Clock()
    rl = limiter(clock)
    for _ in range(3):
        rl.check("alice")
        clock.now += 10  # t = 1000, 1010, 1020

    with pytest.raises(RateLimitedError) as caught:
        rl.check("alice")  # t = 1030: the oldest request (1000) expires at 1060

    assert caught.value.status_code == 429
    assert caught.value.code == "rate_limited"
    assert caught.value.headers == {"Retry-After": "30"}


def test_rejected_requests_do_not_extend_the_block() -> None:
    clock = Clock()
    rl = limiter(clock)
    for _ in range(3):
        rl.check("alice")
    for _ in range(20):
        with pytest.raises(RateLimitedError):
            rl.check("alice")

    clock.now += 60  # the three allowed requests have expired; the 20 rejected ones never counted

    rl.check("alice")


def test_the_window_slides() -> None:
    clock = Clock()
    rl = limiter(clock)
    for _ in range(3):
        rl.check("alice")
    clock.now += 59.9
    with pytest.raises(RateLimitedError):
        rl.check("alice")

    clock.now += 0.2

    rl.check("alice")


def test_each_user_has_their_own_budget() -> None:
    clock = Clock()
    rl = limiter(clock)
    for _ in range(3):
        rl.check("alice")
    with pytest.raises(RateLimitedError):
        rl.check("alice")

    rl.check("bob")  # alice's traffic does not affect bob


def test_retry_after_is_at_least_one_second() -> None:
    clock = Clock()
    rl = limiter(clock, limit=1)
    rl.check("alice")
    clock.now += 59.99

    with pytest.raises(RateLimitedError) as caught:
        rl.check("alice")

    assert caught.value.headers == {"Retry-After": "1"}


def test_it_is_an_app_error_so_it_uses_the_standard_error_format() -> None:
    assert issubclass(RateLimitedError, AppError)


def test_idle_keys_are_evicted_so_memory_stays_bounded() -> None:
    clock = Clock()
    rl = limiter(clock)
    for _ in range(500):
        rl.check(str(uuid.uuid4()))
    assert rl.tracked_keys() == 500

    clock.now += 61
    for _ in range(256):  # a sweep runs every 256 calls
        with contextlib.suppress(RateLimitedError):
            rl.check("someone-new")

    assert rl.tracked_keys() == 1


def test_concurrent_requests_never_exceed_the_limit() -> None:
    clock = Clock()
    rl = limiter(clock, limit=5)
    allowed: list[int] = []

    def hit() -> None:
        try:
            rl.check("alice")
            allowed.append(1)
        except RateLimitedError:
            pass

    threads = [threading.Thread(target=hit) for _ in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(allowed) == 5


@pytest.mark.parametrize(("limit", "window"), [(0, 60), (-1, 60), (5, 0), (5, -3)])
def test_invalid_configuration_is_rejected(limit: int, window: float) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        SlidingWindowRateLimiter(limit=limit, window_seconds=window)
