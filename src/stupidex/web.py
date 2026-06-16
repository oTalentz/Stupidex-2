"""Stupidex web server — Flask + SSE streaming, with auth, CORS, rate limit.

Endpoints are registered in the ``stupidex.routes`` subpackage.

This module is the WSGI entry point (``stupidex.web:app``).
"""

from __future__ import annotations

import logging
import os
import threading
import time
import urllib.parse
from pathlib import Path

from flask import Flask, Response, jsonify, request

from . import db
from . import workspaces as workspaces_module
from .config import DATA_DIR, has_api_key, load_config
from .db_async import run_migrations, record_audit, record_usage
from .llm.handle_input import AGENT_BRIDGE_TOOLS, build_context, execute_workspace_tool, stream_response
from .llm.providers import DEFAULT_FALLBACK_ID, PROVIDERS, list_providers
from .services.auth_service import load_or_create_secret, request_token, set_auth_cookie
from .services.rate_limit import rate_limit_check
from .services.stream_manager import session_lock, claim_stream, get_stream, pop_stream
from .services.validation import (
    MAX_CHAT_IMAGES,
    MAX_CHAT_IMAGE_BYTES,
    validate_chat_images,
    validate_browser_tool_trace,
    validate_git_url,
    path_within,
)

# Try to import structured logging; fall back to basic logging.
try:
    from .logging_config import setup_logging
    _structured_logger = setup_logging()
except Exception:
    _structured_logger = None

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__, static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB upload limit

# ---------------------------------------------------------------------------
# CORS config
# ---------------------------------------------------------------------------

_is_production = (
    os.environ.get("FLASK_ENV") == "production"
    or os.environ.get("RAILWAY_ENV") == "production"
    or os.environ.get("STUPIDEX_SERVER") == "1"
)
_cors_default = "" if _is_production else "*"
_cors_env = os.environ.get("STUPIDEX_CORS", _cors_default).strip()
CORS_ORIGINS = [o.strip() for o in _cors_env.split(",") if o.strip()] if _cors_env else []

# ---------------------------------------------------------------------------
# Secret key
# ---------------------------------------------------------------------------

_SECRET_FILE = DATA_DIR / ".flask_secret"
app.secret_key = load_or_create_secret(_SECRET_FILE)

# ---------------------------------------------------------------------------
# Rate-limit background cleanup thread
# ---------------------------------------------------------------------------

_cleanup_thread = threading.Thread(target=lambda: None, daemon=True)
_cleanup_thread.start()

# ---------------------------------------------------------------------------
# Identity & auth helpers
# ---------------------------------------------------------------------------


def _client_identity() -> str:
    try:
        token = request_token()
        if token:
            u = db.validate_token(token)
            if u:
                return f"u:{u.id}"
    except Exception:
        pass
    return f"ip:{request.remote_addr or 'unknown'}"


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------


def login_required(fn):
    """Validate Bearer token, inject ``request.user``, return 401 on failure."""
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = request_token()
        user = db.validate_token(token)
        if user is None:
            return jsonify({"error": "unauthorized"}), 401
        request.user = user
        return fn(*args, **kwargs)

    return wrapper


def rate_limited(bucket: str):
    """Decorator: enforce per-bucket rate limiting."""
    from functools import wraps

    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not rate_limit_check(bucket, _client_identity()):
                resp = jsonify({"error": "rate limit exceeded"})
                resp.headers["Retry-After"] = "60"
                return resp, 429
            return fn(*args, **kwargs)
        return wrapper
    return deco


# ---------------------------------------------------------------------------
# CORS + security headers middleware
# ---------------------------------------------------------------------------


@app.before_request
def _enforce_browser_origin():
    if request.method not in {"POST", "PATCH", "PUT", "DELETE"}:
        return None
    origin = request.headers.get("Origin", "").rstrip("/")
    if not origin:
        return None
    parsed = urllib.parse.urlparse(origin)
    same_origin = parsed.netloc.lower() == request.host.lower()
    explicitly_allowed = origin in set(CORS_ORIGINS)
    if not same_origin and not explicitly_allowed and CORS_ORIGINS:
        return jsonify({"error": "origin not allowed"}), 403
    return None


@app.after_request
def add_cors(resp):
    origin = request.headers.get("Origin", "")
    if CORS_ORIGINS:
        if origin in set(CORS_ORIGINS):
            resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Credentials"] = "true"
    elif origin:
        resp.headers["Access-Control-Allow-Origin"] = origin
    elif CORS_ORIGINS is None:
        resp.headers["Access-Control-Allow-Origin"] = "*"
    if request.method == "OPTIONS":
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        resp.headers["Access-Control-Max-Age"] = "86400"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    csp_parts = [
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com https://phosphor-icons.com https://js.puter.com",
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com https://phosphor-icons.com",
        "img-src 'self' data: blob: https:",
        "font-src 'self' https://cdn.jsdelivr.net https://fonts.gstatic.com https://phosphor-icons.com",
        "connect-src 'self' https: wss:",
        "frame-ancestors 'none'",
    ]
    resp.headers["Content-Security-Policy"] = "; ".join(csp_parts)
    if resp.mimetype == "text/event-stream":
        resp.headers["X-Accel-Buffering"] = "no"
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.after_request
def _force_utf8_static(resp):
    if resp.mimetype and resp.mimetype.startswith("text/") and resp.mimetype != "text/event-stream":
        resp.headers.set("Content-Type", f"{resp.mimetype}; charset=utf-8")
    return resp


# ===================================================================
# Import routes (must be after app creation and middleware)
# ===================================================================

from .routes import health, auth, providers, sessions, workspaces  # noqa: E402, F401

# ===================================================================
# Entry point
# ===================================================================


def main():
    # Run PostgreSQL migrations (non-blocking on failure)
    try:
        run_migrations()
        app.logger.info("web: migrations applied")
    except Exception as exc:
        app.logger.warning("web: migrations skipped (%s)", exc)

    # Start background worker if Redis available
    try:
        from stupidex.redis_client import get_client as _rc
        from stupidex.worker import start_worker

        if _rc() is not None:
            start_worker("default")
            app.logger.info("web: background worker started")
    except Exception as exc:
        app.logger.warning("web: worker startup skipped (%s)", exc)

    # Log warning if GitHub OAuth is not configured
    from .routes.auth import _github_configured
    if not _github_configured():
        app.logger.warning(
            "GitHub OAuth is not configured. Set GITHUB_CLIENT_ID, "
            "GITHUB_CLIENT_SECRET, and GITHUB_REDIRECT_URI environment variables "
            "to enable private repository cloning. See README.md for details."
        )

    host = os.environ.get("STUPIDEX_HOST", "0.0.0.0")
    port = int(os.environ.get("STUPIDEX_PORT", os.environ.get("PORT", "5000")))
    debug = os.environ.get("STUPIDEX_DEBUG") == "1"
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    main()
