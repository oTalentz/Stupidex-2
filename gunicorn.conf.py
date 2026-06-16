"""Production Gunicorn configuration for Stupidex.

Usage:
  gunicorn -c gunicorn.conf.py stupidex.web:app
  Or via scripts/start-square.sh (auto-detects this file).
"""

import os

# ── Server socket ──────────────────────────────────────────
# Square Cloud requires port 80 (HTTP routed to 443/HTTPS)
bind = os.environ.get("STUPIDEX_BIND", f"0.0.0.0:{os.environ.get('PORT', '80')}")
proc_name = "stupidex"

# ── Worker processes ───────────────────────────────────────
workers = int(os.environ.get("STUPIDEX_WORKERS", "1"))
threads = int(os.environ.get("STUPIDEX_THREADS", "8"))
worker_class = "gthread"

# ── Timeouts ───────────────────────────────────────────────
timeout = int(os.environ.get("STUPIDEX_TIMEOUT", "300"))
graceful_timeout = 30
keepalive = 5

# ── Restart workers periodically to contain memory leaks ───
max_requests = 10000
max_requests_jitter = 1000

# ── Logging ────────────────────────────────────────────────
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("STUPIDEX_LOG_LEVEL", "info")
capture_output = True
enable_stdio_inheritance = True
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# ── Server hooks ───────────────────────────────────────────
def post_fork(server, worker):
    server.log.info("Worker spawned (pid: %s)", worker.pid)

def when_ready(server):
    server.log.info("Stupidex server ready")
