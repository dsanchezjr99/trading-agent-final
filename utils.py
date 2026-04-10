"""
utils.py
Shared utilities: HTTP fetch with exponential-backoff retry, rate limiter.
"""

import time
import threading


def fetch_with_retry(fn, retries: int = 3, delay: float = 2.0):
    """
    Call fn() up to `retries` times with exponential backoff.
    fn must raise on failure (e.g. requests.get + raise_for_status).
    """
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if attempt < retries - 1:
                wait = delay * (2 ** attempt)
                print(f"[retry] Attempt {attempt + 1} failed: {e}. Retrying in {wait:.0f}s...")
                time.sleep(wait)
    raise last_exc  # type: ignore[misc]


class RateLimiter:
    """Enforces a minimum interval between calls (thread-safe)."""

    def __init__(self, calls_per_minute: int):
        self._min_interval = 60.0 / calls_per_minute
        self._last_call: float = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_call
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_call = time.monotonic()
