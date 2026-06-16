"""Health, root, and preflight routes."""

from __future__ import annotations

import os
import time

from flask import jsonify, send_from_directory

from .. import db
from ..web import app


@app.route("/api/<path:_>", methods=["OPTIONS"])
def preflight(_):
    return "", 204


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/health")
def health():
    db_ok = False
    try:
        with db.db_cursor() as cur:
            cur.execute("SELECT 1")
            db_ok = True
    except Exception:
        pass

    pg_ok = False
    try:
        from stupidex.db_async import create_engine
        from sqlalchemy import text as _sa_text
        eng = create_engine()
        if eng:
            with eng.connect() as conn:
                conn.execute(_sa_text("SELECT 1"))
                pg_ok = True
    except Exception:
        pass

    redis_ok = False
    try:
        from stupidex.redis_client import is_available as _redis_avail
        if _redis_avail():
            redis_ok = True
    except Exception:
        pass

    google_ok = bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))
    github_ok = bool(os.environ.get("GITHUB_CLIENT_ID") and os.environ.get("GITHUB_CLIENT_SECRET"))
    active_streams = len(getattr(app, "_stream_instances", {}))

    return jsonify({
        "ok": True,
        "ts": time.time(),
        "v": "1.0.0",
        "db_ok": db_ok,
        "pg_ok": pg_ok,
        "redis_ok": redis_ok,
        "shadow_mode": False,
        "active_streams": active_streams,
        "integrations": {
            "github_configured": github_ok,
            "google_configured": google_ok,
        },
    })
