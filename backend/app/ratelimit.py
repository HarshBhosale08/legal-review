"""In-memory sliding-window rate limiter for login brute-force protection.

Single-process only (per plan §5.3): state is a plain dict guarded by a lock,
keyed by client IP and by the attempted email address. Only FAILED login
attempts count towards the limit so legitimate traffic is never throttled.
"""
import threading
import time
from collections import defaultdict, deque

#: Failed logins allowed per key within the window before 429 is returned.
MAX_FAILED_ATTEMPTS = 5
WINDOW_SECONDS = 60.0


class SlidingWindowLimiter:
    def __init__(self, max_attempts: int = MAX_FAILED_ATTEMPTS, window_seconds: float = WINDOW_SECONDS):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def is_blocked(self, key: str) -> tuple[bool, int]:
        """Return (blocked, retry_after_seconds) for this key."""
        now = time.monotonic()
        with self._lock:
            attempts = self._attempts[key]
            while attempts and now - attempts[0] > self.window_seconds:
                attempts.popleft()
            if len(attempts) >= self.max_attempts:
                retry_after = max(1, int(self.window_seconds - (now - attempts[0])) + 1)
                return True, retry_after
            return False, 0

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._attempts[key].append(time.monotonic())

    def reset(self) -> None:
        """Clear all state (used by tests and after admin unlock in future)."""
        with self._lock:
            self._attempts.clear()


#: Shared limiter for the login endpoint (per-IP and per-email keys).
login_limiter = SlidingWindowLimiter()
