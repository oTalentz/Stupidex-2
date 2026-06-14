"""Production Gunicorn configuration for Stupidex.

Usage:
  gunicorn -c gunicorn.conf.py stupidex.web:app

Or via launcher.py (auto-detects).
"""

import os
import multiprocessing

# ── Server socket ──────────────────────────────────────────
bind = os.environ.get("STUPIDEX_BIND", "0.0.0.0:5000")

# ── Worker processes ───────────────────────────────────────
# For an SSE streaming app with SQLite, more than 1 worker
# is usually counterproductive (SQLite doesn't handle concurrent
# writes well, and each worker opens its own connection).
# Keep at 1 worker; use threads for concurrency.
workers = int(os.environ.get("STUPIDEX_WORKERS", "1"))
threads = int(os.environ.get("STUPIDEX_THREADS", "8"))
worker_class = "gthread"

# ── Timeouts ───────────────────────────────────────────────
# SSE streams can take a while; set a generous timeout.
timeout = int(os.environ.get("STUPIDEX_TIMEOUT", "300"))
graceful_timeout = 30
keepalive = 5

# ── Logging ────────────────────────────────────────────────
accesslog = "-"  # stdout
errorlog = "-"   # stderr
loglevel = os.environ.get("STUPIDEX_LOG_LEVEL", "info")
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# ── Process naming ─────────────────────────────────────────
proc_name = "stupidex"

# ── Server hooks ───────────────────────────────────────────
def on_starting(server):
    """Called just before the master process is initialized."""
    pass

def post_fork(server, worker):
    """Called just after a worker has been forked."""
    server.log.info("Worker spawned (pid: %s)", worker.pid)

def pre_exec(server):
    """Called just before a new master process is forked."""
    server.log.info("Forked child, re-executing.")

def when_ready(server):
    """Called just after the server is started."""
    server.log.info("Stupidex server is ready. Spawning workers...")

def worker_int(worker):
    """Called when a worker receives the INT or QUIT signal."""
    pass

def worker_abort(worker):
    """Called when a worker receives the SIGABRT signal."""
    worker.log.info("Worker received SIGABRT")
