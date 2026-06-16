"""Redis abstraction layer — rate limits, locks, job queue, dynamic config.

Environment:
  REDIS_URL=redis://user:pass@host:6379/0  → production
  REDIS_URL unset                            → no-op fallback (in-memory)

All functions degrade gracefully when Redis is unavailable — the app
still works, just without shared state across instances.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

_client = None
_redis_available = False

# In-memory fallbacks for when Redis is not configured
_in_memory_locks: dict[str, float] = {}
_in_memory_rate: dict[str, list[float]] = {}
_in_memory_cache: dict[str, tuple[Any, float]] = {}
_in_memory_lock = threading.Lock()


def get_redis_url() -> str:
    return os.environ.get("REDIS_URL", "").strip()


def get_client():
    """Lazy-init and return the Redis client (or None)."""
    global _client, _redis_available
    if _client is not None:
        return _client
    url = get_redis_url()
    if not url:
        _redis_available = False
        return None
    try:
        import redis as _redis_mod
        _client = _redis_mod.from_url(
            url,
            decode_responses=True,
            socket_timeout=2,
            socket_connect_timeout=2,
            retry_on_timeout=True,
            health_check_interval=30,
        )
        _client.ping()
        _redis_available = True
        logger.info("redis_client: connected to %s", url.split("@")[-1] if "@" in url else "Redis")
    except Exception as e:
        logger.warning("redis_client: connection failed (%s), using in-memory fallback", e)
        _client = None
        _redis_available = False
    return _client


def is_available() -> bool:
    """Check if Redis is connected."""
    if not _redis_available:
        return False
    try:
        c = get_client()
        if c is None:
            return False
        return c.ping()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


def rate_limit(
    key: str,
    max_requests: int,
    window_seconds: int = 60,
) -> tuple[bool, int, int]:
    """Check if `key` has exceeded rate limit.

    Returns (allowed, remaining, reset_after_seconds).
    """
    now = time.time()
    client = get_client()

    if client:
        try:
            pipe = client.pipeline()
            pipe.zremrangebyscore(key, 0, now - window_seconds)
            pipe.zcard(key)
            pipe.zadd(key, {str(now): now})
            pipe.expire(key, window_seconds)
            _, count, _, _ = pipe.execute()
            allowed = int(count) < max_requests
            remaining = max(0, max_requests - int(count))
            reset_after = int(window_seconds - (now - (now // 1)))
            return allowed, remaining, reset_after
        except Exception as e:
            logger.debug("redis rate_limit error: %s", e)

    # In-memory fallback
    with _in_memory_lock:
        timestamps = _in_memory_rate.get(key, [])
        cutoff = now - window_seconds
        timestamps = [t for t in timestamps if t > cutoff]
        if len(timestamps) >= max_requests:
            _in_memory_rate[key] = timestamps
            return False, 0, int(timestamps[0] + window_seconds - now)
        timestamps.append(now)
        _in_memory_rate[key] = timestamps
        remaining = max_requests - len(timestamps)
        return True, remaining, 0


# ---------------------------------------------------------------------------
# Distributed lock
# ---------------------------------------------------------------------------


@contextmanager
def lock(key: str, ttl: int = 30, blocking: bool = False, timeout: float = 10.0):
    """Acquire a distributed lock. Yields True if acquired, False otherwise."""
    lock_id = f"lock:{key}"
    client = get_client()
    acquired = False

    if client:
        try:
            import uuid
            my_id = uuid.uuid4().hex[:16]
            deadline = time.monotonic() + timeout
            while True:
                if client.setnx(lock_id, my_id):
                    client.expire(lock_id, ttl)
                    acquired = True
                    break
                if not blocking:
                    break
                if time.monotonic() > deadline:
                    break
                time.sleep(0.05)
        except Exception as e:
            logger.debug("redis lock error: %s", e)

        try:
            yield acquired
        finally:
            if acquired:
                try:
                    # Only delete if we still own it
                    if client.get(lock_id) == my_id:
                        client.delete(lock_id)
                except Exception:
                    pass
        return

    # In-memory fallback (process-local only)
    with _in_memory_lock:
        if _in_memory_locks.get(key, 0) > time.time():
            yield False
            return
        _in_memory_locks[key] = time.time() + ttl
        try:
            yield True
        finally:
            with _in_memory_lock:
                _in_memory_locks.pop(key, None)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def cache_set(key: str, value: Any, ttl: int = 300) -> None:
    """Set a cached value."""
    client = get_client()
    if client:
        try:
            client.setex(f"cache:{key}", ttl, json.dumps(value))
            return
        except Exception as e:
            logger.debug("redis cache_set error: %s", e)
    # In-memory fallback
    with _in_memory_lock:
        _in_memory_cache[key] = (value, time.time() + ttl)


def cache_get(key: str) -> Any | None:
    """Get a cached value, or None if expired/missing."""
    client = get_client()
    if client:
        try:
            raw = client.get(f"cache:{key}")
            if raw is not None:
                return json.loads(raw)
            return None
        except Exception as e:
            logger.debug("redis cache_get error: %s", e)
            return None
    # In-memory fallback
    with _in_memory_lock:
        entry = _in_memory_cache.get(key)
        if entry is None:
            return None
        val, expiry = entry
        if time.time() > expiry:
            _in_memory_cache.pop(key, None)
            return None
        return val


# ---------------------------------------------------------------------------
# Job queue (simple list-based)
# ---------------------------------------------------------------------------


def enqueue(queue_name: str, job: dict) -> bool:
    """Push a job onto a Redis list."""
    client = get_client()
    if client:
        try:
            client.lpush(f"queue:{queue_name}", json.dumps(job))
            return True
        except Exception as e:
            logger.warning("redis enqueue error: %s", e)
    return False


def dequeue(queue_name: str, timeout: int = 5) -> dict | None:
    """Blocking pop from a queue."""
    client = get_client()
    if client:
        try:
            result = client.brpop(f"queue:{queue_name}", timeout=timeout)
            if result:
                return json.loads(result[1])
        except Exception as e:
            logger.warning("redis dequeue error: %s", e)
    return None


def queue_length(queue_name: str) -> int:
    client = get_client()
    if client:
        try:
            return client.llen(f"queue:{queue_name}")
        except Exception:
            pass
    return 0


# ---------------------------------------------------------------------------
# Pub/Sub
# ---------------------------------------------------------------------------


def publish(channel: str, message: Any) -> None:
    """Publish a message to a channel."""
    client = get_client()
    if client:
        try:
            client.publish(f"pubsub:{channel}", json.dumps(message))
        except Exception as e:
            logger.debug("redis publish error: %s", e)


# ---------------------------------------------------------------------------
# Dynamic config (stored in Redis hash)
# ---------------------------------------------------------------------------


def config_set(key: str, value: Any) -> None:
    client = get_client()
    if client:
        try:
            client.hset("config", key, json.dumps(value))
        except Exception:
            pass


def config_get(key: str, default: Any = None) -> Any:
    client = get_client()
    if client:
        try:
            raw = client.hget("config", key)
            if raw is not None:
                return json.loads(raw)
        except Exception:
            pass
    return default


def config_getall() -> dict:
    client = get_client()
    if client:
        try:
            raw = client.hgetall("config")
            return {k: json.loads(v) for k, v in raw.items()}
        except Exception:
            pass
    return {}
