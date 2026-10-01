"""In-memory sliding-window rate limiter, keyed by an arbitrary string (here: the user id).

PER INSTANCE: the counters live in this process's memory. Two server instances each keep their
own counters, so with N instances a user could send up to N times the limit. The deployment
therefore runs a single instance (Render's free plan allows exactly one, see docs/deploy.md).
Counters are lost on restart and on spin-down.
"""

import math
import threading
import time
from collections import deque
from collections.abc import Callable

from app.core.errors import AppError


class RateLimitedError(AppError):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(
            code="rate_limited",
            message="Too many requests. Please wait a moment and try again.",
            status_code=429,
            headers={"Retry-After": str(retry_after_seconds)},
        )


class SlidingWindowRateLimiter:
    """Allows `limit` requests per `window_seconds` for each key. Only allowed requests count,
    so a client that keeps hammering while blocked is not punished for longer."""

    _SWEEP_EVERY = 256  # calls between sweeps of idle keys

    def __init__(
        self,
        *,
        limit: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit <= 0 or window_seconds <= 0:
            raise ValueError("limit and window_seconds must be positive")
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._calls = 0

    def check(self, key: str) -> None:
        """Record a request for `key`, or raise RateLimitedError (429 with Retry-After)."""
        with self._lock:
            now = self._clock()
            self._calls += 1
            if self._calls % self._SWEEP_EVERY == 0 or len(self._hits) > 10_000:
                self._sweep(now)
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] >= self._window:
                hits.popleft()
            if len(hits) >= self._limit:
                retry_after = math.ceil(self._window - (now - hits[0]))
                raise RateLimitedError(max(1, retry_after))
            hits.append(now)

    def tracked_keys(self) -> int:
        with self._lock:
            return len(self._hits)

    def _sweep(self, now: float) -> None:
        idle = [k for k, hits in self._hits.items() if not hits or now - hits[-1] >= self._window]
        for key in idle:
            del self._hits[key]
