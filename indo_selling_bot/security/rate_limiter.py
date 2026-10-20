"""
In-memory sliding window rate limiter for login protection and abuse mitigation.
"""
import time
from collections import defaultdict
from indo_selling_bot.config import settings

class SlidingWindowRateLimiter:
    def __init__(self, max_requests: int = 5, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str) -> tuple[bool, int]:
        """
        Check if request is allowed for the given key.
        Returns: (is_allowed: bool, retry_after_seconds: int)
        """
        now = time.time()
        window_start = now - self.window_seconds

        # Filter out expired timestamps
        valid_timestamps = [t for t in self.requests[key] if t > window_start]
        self.requests[key] = valid_timestamps

        if len(valid_timestamps) >= self.max_requests:
            # Earliest timestamp + window = time when an attempt frees up
            earliest = valid_timestamps[0]
            retry_after = max(1, int(earliest + self.window_seconds - now))
            return False, retry_after

        return True, 0

    def record_attempt(self, key: str) -> None:
        """Record an attempt (e.g., failed login)."""
        self.requests[key].append(time.time())

    def reset(self, key: str) -> None:
        """Reset limits for a key (e.g., on successful login)."""
        if key in self.requests:
            del self.requests[key]

# Pre-configured instances
login_rate_limiter = SlidingWindowRateLimiter(
    max_requests=settings.RATE_LIMIT_LOGIN_ATTEMPTS,
    window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS
)

api_rate_limiter = SlidingWindowRateLimiter(
    max_requests=60,
    window_seconds=60
)
