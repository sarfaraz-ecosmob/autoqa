"""Redis-backed rate limiting (spec §25)."""
import time

from app.tasks import get_redis


class RateLimiter:
    def __init__(self, limit: int = 100, window_seconds: int = 60, redis_client=None):
        self.limit = limit
        self.window = window_seconds
        self._redis = redis_client  # injectable for tests

    def check(self, key: str) -> bool:
        """Return True if allowed, incrementing the counter for this window."""
        r = self._redis if self._redis is not None else get_redis()
        if r is None:
            return True  # fail-open if redis unavailable (dev only)
        now = time.time()
        window_key = f"ratelimit:{key}:{int(now // self.window)}"
        count = r.incr(window_key)
        if count == 1:
            r.expire(window_key, self.window + 1)
        return count <= self.limit
