"""Rate limiter extracted from web.py — per-bucket in-memory sliding window."""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict

_RL_LOCK = threading.Lock()
_RL_BUCKETS: dict[str, list[float]] = defaultdict(list)
_RL_RULES: list[tuple[str, int, float]] = [
    ("auth", 10, 60.0),
    ("chat", 60, 60.0),
    ("upload", 20, 60.0),
    ("default", 240, 60.0),
]


def _prune_stale():
    """Remove buckets that have no recent activity (called internally)."""
    now = time.time()
    max_window = max(rule[2] for rule in _RL_RULES)
    with _RL_LOCK:
        stale = [k for k, values in _RL_BUCKETS.items() if not values or values[-1] < now - max_window]
        for k in stale:
            _RL_BUCKETS.pop(k, None)


def rate_limit_check(bucket: str, identity: str) -> bool:
    """Returns True if allowed, False if 429."""
    rule = next((r for r in _RL_RULES if r[0] == bucket), _RL_RULES[-1])
    name, max_req, window = rule
    key = f"{name}:{identity}"
    now = time.time()
    with _RL_LOCK:
        if len(_RL_BUCKETS) > 10_000:
            cutoff = now - max(rule[2] for rule in _RL_RULES)
            stale = [k for k, values in _RL_BUCKETS.items() if not values or values[-1] < cutoff]
            for k in stale:
                _RL_BUCKETS.pop(k, None)
        if key not in _RL_BUCKETS and len(_RL_BUCKETS) >= 20_000:
            return False
        history = _RL_BUCKETS[key]
        while history and history[0] < now - window:
            history.pop(0)
        if len(history) >= max_req:
            return False
        history.append(now)
        return True
