"""Slows password guessing by counting recent failures.

In-memory is enough for the single API process used here. A restart clears the
counts, which only ever makes login more permissive for a moment.
"""

import threading
import time
from collections import defaultdict, deque

WINDOW_SECONDS = 300
MAX_FAILURES_PER_SOURCE = 10
MAX_FAILURES_PER_EMAIL = 50


class LoginThrottle:
    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _recent(self, key: str, now: float) -> int:
        entries = self._failures[key]
        while entries and now - entries[0] > WINDOW_SECONDS:
            entries.popleft()
        if not entries:
            del self._failures[key]
            return 0
        return len(entries)

    def is_blocked(self, email: str, source: str) -> bool:
        now = self._clock()
        with self._lock:
            return (
                self._recent(f"src:{email}:{source}", now) >= MAX_FAILURES_PER_SOURCE
                or self._recent(f"email:{email}", now) >= MAX_FAILURES_PER_EMAIL
            )

    def record_failure(self, email: str, source: str) -> None:
        now = self._clock()
        with self._lock:
            self._failures[f"src:{email}:{source}"].append(now)
            self._failures[f"email:{email}"].append(now)

    def clear_source(self, email: str, source: str) -> None:
        with self._lock:
            self._failures.pop(f"src:{email}:{source}", None)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()


login_throttle = LoginThrottle()
