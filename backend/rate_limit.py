"""Thread-safe in-memory sliding-window rate limiter for authentication endpoints."""
import os
import threading
import time
from collections import defaultdict, deque

_LOCK = threading.Lock()
_FAILURES: dict[str, deque[float]] = defaultdict(deque)


def _disabled() -> bool:
    return os.environ.get("DISABLE_LOGIN_RATE_LIMIT", "").strip().lower() in {"1", "true", "yes", "on"}


def _window_seconds() -> int:
    return int(os.environ.get("LOGIN_RATE_LIMIT_WINDOW_SEC", "300"))


def _max_failures() -> int:
    return int(os.environ.get("LOGIN_RATE_LIMIT_MAX_ATTEMPTS", "15"))


def is_rate_limited(key: str) -> tuple[bool, int]:
    """Return (limited, retry_after_seconds) for the given rate-limit key."""
    if _disabled():
        return False, 0
    now = time.monotonic()
    window = _window_seconds()
    limit = _max_failures()
    with _LOCK:
        bucket = _FAILURES[key]
        while bucket and (now - bucket[0]) > window:
            bucket.popleft()
        if len(bucket) >= limit:
            retry_after = max(1, int(window - (now - bucket[0])))
            return True, retry_after
        return False, 0


def record_failure(key: str) -> None:
    if _disabled():
        return
    now = time.monotonic()
    window = _window_seconds()
    with _LOCK:
        bucket = _FAILURES[key]
        while bucket and (now - bucket[0]) > window:
            bucket.popleft()
        bucket.append(now)


def clear_failures(key: str) -> None:
    with _LOCK:
        _FAILURES.pop(key, None)
