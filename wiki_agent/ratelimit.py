"""In-memory sliding-window rate limiter — no external dependencies.

A personal-scale guard so a runaway client (or a leaked token) can't hammer the
API. Keyed by an arbitrary string (default: one global bucket for the single
shared token). Thread-safe.
"""
from __future__ import annotations
import time
import threading
from collections import defaultdict, deque

_lock = threading.Lock()
_hits: dict[str, deque] = defaultdict(deque)


def check_rate(key: str, limit: int, window_s: float) -> bool:
    """Record a hit for `key`; return True if allowed, False if over `limit`
    within the last `window_s` seconds. A non-positive limit disables limiting."""
    if limit <= 0:
        return True
    now = time.monotonic()
    cutoff = now - window_s
    with _lock:
        dq = _hits[key]
        while dq and dq[0] < cutoff:
            dq.popleft()
        if not dq:
            # Window fully expired — evict then re-create so idle keys don't
            # accumulate empty deques forever (matters if keyed per-client).
            _hits.pop(key, None)
            _hits[key].append(now)
            return True
        if len(dq) >= limit:
            return False
        dq.append(now)
        return True


def reset(key: str | None = None) -> None:
    """Clear counters (all, or one key). Mainly for tests."""
    with _lock:
        if key is None:
            _hits.clear()
        else:
            _hits.pop(key, None)
