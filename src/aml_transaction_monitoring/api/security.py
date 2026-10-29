"""API-key check and a basic in-memory rate limiter."""

import secrets
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable


def key_matches(given: str | None, expected: str) -> bool:
    """Constant-time comparison, so timing does not reveal how much of a key matched."""
    if not given:
        return False
    return secrets.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


class RateLimiter:
    """At most `limit` requests per client in any `window` seconds (sliding window).

    In memory, one process: enough for a single local service. Behind several
    workers or replicas it would need a shared store such as Redis.
    """

    def __init__(
        self, limit: int, window: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit = limit
        self.window = window
        self.clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, client: str) -> float:
        """Record a request; return 0 if allowed, else the seconds to wait."""
        with self._lock:
            now = self.clock()
            hits = self._hits[client]
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return hits[0] + self.window - now
            hits.append(now)
            return 0.0
