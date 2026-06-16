"""Stream manager — per-session stream claims and cancellation."""

from __future__ import annotations

import threading

_SESSION_LOCKS: dict[str, threading.Lock] = {}
_SESSION_LOCKS_GUARD = threading.Lock()
_STREAMS_LOCK = threading.Lock()
_STREAMS: dict[str, threading.Event] = {}


def session_lock(session_id: str) -> threading.Lock:
    with _SESSION_LOCKS_GUARD:
        if session_id not in _SESSION_LOCKS:
            _SESSION_LOCKS[session_id] = threading.Lock()
        return _SESSION_LOCKS[session_id]


def claim_stream(sid: str) -> threading.Event | None:
    with _STREAMS_LOCK:
        current = _STREAMS.get(sid)
        if current is not None:
            # If the existing stream's cancel event is already set, the producer
            # is done (or being torn down) — safe to reclaim.
            if current.is_set():
                _STREAMS.pop(sid, None)
            else:
                return None
        ev = threading.Event()
        _STREAMS[sid] = ev
        return ev


def get_stream(sid: str) -> threading.Event | None:
    with _STREAMS_LOCK:
        return _STREAMS.get(sid)


def pop_stream(sid: str, expected: threading.Event) -> None:
    with _STREAMS_LOCK:
        if _STREAMS.get(sid) is expected:
            _STREAMS.pop(sid, None)
