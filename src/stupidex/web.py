"""Stupidex web server — Flask + SSE streaming, with auth, CORS, rate limit.

Endpoints:
  GET  /                                  → SPA
  GET  /api/health                        → liveness probe

  POST /api/auth/register                 → {username, password} → {user, token}
  POST /api/auth/login                    → {username, password} → {user, token}
  POST /api/auth/logout                   → invalidate token (login_required)
  GET  /api/auth/me                       → current user info (login_required)

  GET  /api/providers                     → provider list
  GET  /api/config                        → current config (no secrets leaked)
  POST /api/config                        → update provider/model/api_key

  GET  /api/sessions                      → list sessions (newest first, per-user)
  POST /api/sessions                      → create session (per-user)
  PATCH /api/sessions/<id>                → rename / pin / archive
  DELETE /api/sessions/<id>               → delete
  GET  /api/sessions/search?q=...         → search by title/content (per-user)
  GET  /api/sessions/<id>/messages        → message history
  POST /api/sessions/<id>/clear           → clear messages (keep session)
  POST /api/sessions/<id>/regenerate      → redo last assistant turn
  POST /api/sessions/<id>/stop            → cancel current stream
  POST /api/sessions/<id>/chat            → SSE stream
  GET  /api/sessions/<id>/export         → download JSON or Markdown

  GET  /api/workspaces                    → list workspaces (per-user)
  POST /api/workspaces                    → create empty workspace (per-user)
  DELETE /api/workspaces/<id>             → delete workspace (per-user)
  POST /api/workspaces/<id>/activate      → set active workspace (per-user)
  POST /api/workspaces/<id>/upload        → upload files (multipart)
  POST /api/workspaces/<id>/upload-zip    → upload + extract zip
  POST /api/workspaces/<id>/clone         → git clone
  POST /api/workspaces/<id>/pull          → git pull
  GET  /api/workspaces/<id>/tree         → file tree
  GET  /api/workspaces/<id>/file?path=   → file content
"""
import io
import ipaddress
import json
import os
import queue
import re
import secrets
import socket
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from collections.abc import Generator
from functools import wraps
from pathlib import Path

from flask import Flask, Response, jsonify, redirect, request, send_from_directory

from . import db, workspaces as workspaces_module
from .config import DATA_DIR, has_api_key, load_config, update_config
from .llm.handle_input import build_context, stream_response
from .llm.providers import DEFAULT_FALLBACK_ID, PROVIDERS, list_providers

app = Flask(__name__, static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB upload limit (DoS hardening)

# CORS: when STUPIDEX_CORS is unset, allow only same-origin (no header).
# Set to a comma-separated list of origins or "*" to allow any.
_cors_env = os.environ.get("STUPIDEX_CORS", "").strip()
CORS_ORIGINS = [o.strip() for o in _cors_env.split(",") if o.strip()] if _cors_env else []

# Google OAuth
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.environ.get(
    "GOOGLE_REDIRECT_URI",
    (os.environ.get("FRONTEND_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "http://localhost:" + os.environ.get("PORT", "5000"))
    + "/api/auth/google/callback"
)
_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

# Session-secret used to sign the OAuth `state` cookie (CSRF defense).
# Auto-generated on first run and persisted to the data dir.
_SECRET_FILE = DATA_DIR / ".flask_secret"
def _load_or_create_secret() -> bytes:
    try:
        if _SECRET_FILE.exists():
            return _SECRET_FILE.read_bytes()
    except Exception:
        pass
    secret = secrets.token_bytes(32)
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _SECRET_FILE.write_bytes(secret)
        _SECRET_FILE.chmod(0o600)
    except Exception:
        pass
    return secret

app.secret_key = _load_or_create_secret()

# In-memory rate limiter (per IP+user). Sliding window.
_RL_LOCK = threading.Lock()
_RL_BUCKETS: dict[str, list[float]] = defaultdict(list)
_RL_RULES: list[tuple[str, int, float]] = [
    # (name, max_requests, window_seconds)
    ("auth",     10,  60.0),   # /api/auth/*   — login/register/logout
    ("chat",     60,  60.0),   # /chat, /regenerate, /stop
    ("upload",   20,  60.0),   # upload, clone
    ("default", 240,  60.0),   # everything else
]


def _rate_limit_check(bucket: str, identity: str) -> bool:
    """Returns True if the request is allowed, False if it should be 429'd."""
    rule = next((r for r in _RL_RULES if r[0] == bucket), _RL_RULES[-1])
    name, max_req, window = rule
    key = f"{name}:{identity}"
    now = time.time()
    with _RL_LOCK:
        history = _RL_BUCKETS[key]
        # drop old
        while history and history[0] < now - window:
            history.pop(0)
        if len(history) >= max_req:
            return False
        history.append(now)
        return True


def _client_identity() -> str:
    """Stable identity for rate limiting: user_id if logged, else remote IP."""
    try:
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if token:
            u = db.validate_token(token)
            if u:
                return f"u:{u.id}"
    except Exception:
        pass
    return f"ip:{request.remote_addr or 'unknown'}"


# OAuth `state` cookie store (CSRF defense)
_OAUTH_STATE_COOKIE = "stupidex_oauth_state"


# ============================================================
# Auth decorator
# ============================================================

def login_required(fn):
    """Validate Bearer token header against db.validate_token().

    On success, injects ``request.user`` as a ``db.User`` dataclass.
    Returns 401 JSON on missing/invalid/expired token.
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        raw = request.headers.get("Authorization", "")
        token = raw.removeprefix("Bearer ").strip()
        user = db.validate_token(token)
        if user is None:
            return jsonify({"error": "unauthorized"}), 401
        request.user = user
        return fn(*args, **kwargs)
    return wrapper


def rate_limited(bucket: str):
    """Decorator: enforce rate limit on a route."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not _rate_limit_check(bucket, _client_identity()):
                resp = jsonify({"error": "rate limit exceeded"})
                resp.headers["Retry-After"] = "60"
                return resp, 429
            return fn(*args, **kwargs)
        return wrapper
    return deco


# ============================================================
# CORS + security headers
# ============================================================

@app.after_request
def add_cors(resp):
    origin = request.headers.get("Origin", "")
    if CORS_ORIGINS:
        if "*" in CORS_ORIGINS:
            resp.headers["Access-Control-Allow-Origin"] = "*"
        elif origin and origin in CORS_ORIGINS:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
            resp.headers["Access-Control-Allow-Credentials"] = "true"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
        resp.headers["Access-Control-Max-Age"] = "600"

    # Security headers (no X-Frame-Options to keep iframe embedding iframes possible,
    # but CSP limits where scripts can load from).
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("X-XSS-Protection", "0")
    # CSP: same-origin by default; allow marked/highlight.js CDNs explicitly.
    # NOTE: 'unsafe-inline' on script-src is required for the OAuth callback
    # inline script that saves the token to localStorage. No user-generated
    # content is ever placed in inline scripts, so this is safe.
    if "Content-Security-Policy" not in resp.headers:
        csp = (
            "default-src 'self'; "
            "img-src 'self' data: https:; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "font-src 'self' data: https://cdn.jsdelivr.net https://fonts.gstatic.com; "
            "connect-src 'self'"
        )
        resp.headers["Content-Security-Policy"] = csp

    # SSE: prevent buffering on proxies
    if resp.mimetype == "text/event-stream":
        resp.headers["X-Accel-Buffering"] = "no"
        resp.headers["Cache-Control"] = "no-cache"

    return resp


@app.route("/api/<path:_>", methods=["OPTIONS"])
def preflight(_):
    return Response("", 204)


# ============================================================
# Static / health
# ============================================================

@app.route("/")
def index():
    resp = send_from_directory(app.static_folder, "index.html")
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp


# Ensure all static text/* files are served as UTF-8 so non-ASCII
# characters (emojis, accented letters) render correctly in the browser.
@app.after_request
def _force_utf8_static(resp):
    if not request.path.startswith("/static/"):
        return resp
    ct = resp.headers.get("Content-Type", "")
    if ct.startswith("text/") and "charset" not in ct:
        resp.headers["Content-Type"] = ct + "; charset=utf-8"
    elif ct.startswith("application/javascript") and "charset" not in ct:
        resp.headers["Content-Type"] = "application/javascript; charset=utf-8"
    elif ct == "application/octet-stream":
        ext = request.path.rsplit(".", 1)[-1].lower()
        mapping = {
            "js": "application/javascript; charset=utf-8",
            "css": "text/css; charset=utf-8",
            "html": "text/html; charset=utf-8",
            "svg": "image/svg+xml; charset=utf-8",
        }
        if ext in mapping:
            resp.headers["Content-Type"] = mapping[ext]
    return resp


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "ts": time.time(), "v": "oauth-fix-v3"})


# ============================================================
# Google OAuth
# ============================================================

@app.route("/api/auth/google", methods=["GET"])
@rate_limited("auth")
def auth_google():
    if not GOOGLE_CLIENT_ID:
        return jsonify({"error": "Google OAuth not configured (set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)"}), 501

    state = secrets.token_urlsafe(24)
    # CSRF: bind the state to a signed cookie. Callback must match.
    nonce = secrets.token_urlsafe(16)
    params = urllib.parse.urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": f"{state}.{nonce}",
        "access_type": "offline",
        "prompt": "select_account",
    })
    resp = redirect(f"{_GOOGLE_AUTH_URL}?{params}")
    # HttpOnly, SameSite=Lax — the cookie survives the OAuth redirect.
    resp.set_cookie(
        _OAUTH_STATE_COOKIE,
        f"{state}.{nonce}",
        max_age=600,
        httponly=True,
        secure=request.is_secure,
        samesite="Lax",
        path="/",
    )
    return resp


@app.route("/api/auth/google/callback", methods=["GET"])
@rate_limited("auth")
def auth_google_callback():
    code = request.args.get("code", "")
    error = request.args.get("error", "")
    if error:
        return jsonify({"error": f"Google OAuth denied: {error}"}), 400
    if not code:
        return jsonify({"error": "missing authorization code"}), 400

    # CSRF: validate `state` against the cookie (constant-time compare).
    state_qs = request.args.get("state", "")
    state_cookie = request.cookies.get(_OAUTH_STATE_COOKIE, "")
    if not state_qs or not state_cookie or not secrets.compare_digest(state_qs, state_cookie):
        return jsonify({"error": "invalid OAuth state (possible CSRF)"}), 400

    # Exchange code for access token
    try:
        token_req = urllib.request.Request(
            _GOOGLE_TOKEN_URL,
            data=urllib.parse.urlencode({
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": GOOGLE_REDIRECT_URI,
            }).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(token_req, timeout=10) as resp:
            token_data = json.loads(resp.read())
        access_token = token_data.get("access_token")
        if not access_token:
            return jsonify({"error": "failed to exchange code", "detail": token_data.get("error_description", "")}), 400
    except Exception as e:
        return jsonify({"error": f"token exchange failed: {e}"}), 500

    # Get user info from Google
    try:
        userinfo_req = urllib.request.Request(
            _GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        with urllib.request.urlopen(userinfo_req, timeout=10) as resp:
            userinfo = json.loads(resp.read())
        email = (userinfo.get("email") or "").strip().lower()
        name = userinfo.get("name") or email.split("@")[0]
        picture = userinfo.get("picture") or ""
        if not email:
            return jsonify({"error": "Google did not return an email address"}), 400
    except Exception as e:
        return jsonify({"error": f"userinfo request failed: {e}"}), 500

    # Find or create user
    try:
        user, token = db.find_or_create_oauth_user(email, name, picture, "google")
    except Exception as e:
        return jsonify({"error": f"user creation failed: {e}"}), 500

    user_dict = user.to_dict()
    user_dict["token"] = token

    # Return HTML that saves the token and redirects to the frontend.
    # The token is delivered over a one-shot HTML page. The CSP allows 'self' scripts
    # only, so we set the token via a meta refresh + same-origin script tag.
    # If FRONTEND_URL is not set, fall back to the current origin root
    # (so the redirect lands on the app, not on a sub-route like the
    # callback itself).
    # If FRONTEND_URL is not set, construct the origin from the request.
    # Square Cloud sits behind Cloudflare which sets X-Forwarded-Proto.
    frontend = os.environ.get("FRONTEND_URL")
    if not frontend:
        try:
            scheme = request.headers.get("X-Forwarded-Proto", "https")
            host = request.host.rstrip("/")
            frontend = f"{scheme}://{host}"
        except Exception:
            frontend = "/"

    # The callback must save the token and redirect to the app. We use
    # JavaScript as the primary mechanism (sets localStorage then redirects).
    # The per-response CSP allows 'unsafe-inline' so the inline script runs.
    # A meta-refresh fallback fires after 3 seconds if JS is completely
    # disabled (rare — in that case, the user must log in again manually
    # because we can't use localStorage without JS).
    body = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="3; url={frontend}">
<title>Login — Stupidex</title>
<style>
  body {{ font: 14px -apple-system, system-ui, sans-serif; background: #09090b; color: #f5f5f7; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
  a {{ color: #6d5dfc; }}
</style>
<script>
(function() {{
  try {{
    localStorage.setItem('stupidex-token', {json.dumps(token)});
    localStorage.setItem('stupidex-user', {json.dumps(json.dumps(user_dict))});
    // Redirect immediately — the localStorage write is synchronous so
    // the token is guaranteed to be persisted before navigation.
    window.location.replace({json.dumps(frontend)});
  }} catch (e) {{
    // If JS fails, the meta-refresh (3s delay) handles the redirect.
  }}
}})();
</script>
</head>
<body>
<p>Login OK. Indo para <a href="{frontend}">Stupidex</a>…</p>
</body>
</html>"""
    resp = Response(body, mimetype="text/html; charset=utf-8")
    # Clear the one-shot state cookie
    resp.set_cookie(_OAUTH_STATE_COOKIE, "", max_age=0, path="/")
    # Per-response CSP: allow inline scripts so the token save JS runs.
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline'"
    )
    return resp


# ============================================================
# Auth endpoints (email/password)
# ============================================================

@app.route("/api/auth/register", methods=["POST"])
@rate_limited("auth")
def auth_register():
    data = request.get_json(force=True) or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    try:
        user, token = db.create_user(username, password)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"user": user.to_dict(), "token": token})


@app.route("/api/auth/login", methods=["POST"])
@rate_limited("auth")
def auth_login():
    data = request.get_json(force=True) or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    try:
        user, token = db.authenticate_user(username, password)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 401
    return jsonify({"user": user.to_dict(), "token": token})


@app.route("/api/auth/logout", methods=["POST"])
@login_required
@rate_limited("auth")
def auth_logout():
    raw = request.headers.get("Authorization", "")
    token = raw.removeprefix("Bearer ").strip()
    db.logout_token(token)
    return jsonify({"ok": True})


@app.route("/api/auth/me", methods=["GET"])
@login_required
@rate_limited("default")
def auth_me():
    return jsonify({"user": request.user.to_dict()})


# ============================================================
# Providers / Config
# ============================================================

@app.route("/api/providers", methods=["GET"])
@login_required
def providers():
    return jsonify(list_providers())


@app.route("/api/config", methods=["GET"])
@login_required
def get_config():
    cfg = load_config()
    return jsonify({
        "provider": cfg.provider,
        "model": cfg.model,
        "custom_model": cfg.custom_model,
        "has_api_key": bool(cfg.api_key),
        "base_url": cfg.base_url,
    })


@app.route("/api/config", methods=["POST"])
@login_required
def set_config():
    data = request.get_json(force=True) or {}
    provider = (data.get("provider") or "").strip() or None
    model = (data.get("model") or "").strip() or None
    custom_model = data.get("custom_model", None)
    api_key = data.get("api_key", None)
    if provider and provider not in PROVIDERS:
        return jsonify({"error": f"unknown provider: {provider}"}), 400

    update = {"provider": provider, "model": model}
    if custom_model is not None:
        update["custom_model"] = custom_model.strip()
    if api_key:
        update["api_key"] = api_key.strip()
    elif data.get("clear_api_key"):
        from .config import load_config as _load, save_config as _save
        c = _load()
        c.pop("api_key", None)
        _save(c)

    cfg = update_config(**update)
    return jsonify({
        "ok": True,
        "has_api_key": bool(cfg.api_key),
        "provider": cfg.provider,
        "model": cfg.model,
        "custom_model": cfg.custom_model,
    })


# ============================================================
# Sessions
# ============================================================

@app.route("/api/sessions", methods=["GET"])
@login_required
@rate_limited("default")
def sessions_list():
    include_archived = request.args.get("include_archived") == "1"
    return jsonify([s.to_dict() for s in db.list_sessions(request.user.id, include_archived=include_archived)])


@app.route("/api/sessions/search", methods=["GET"])
@login_required
@rate_limited("default")
def sessions_search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify([])
    if len(q) > 200:
        return jsonify({"error": "query too long"}), 400
    return jsonify([s.to_dict() for s in db.search_sessions(request.user.id, q)])


@app.route("/api/sessions", methods=["POST"])
@login_required
@rate_limited("default")
def sessions_create():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    provider = data.get("provider") or cfg.provider
    if provider not in PROVIDERS:
        provider = DEFAULT_FALLBACK_ID
    model = (data.get("model") or cfg.model or "").strip()[:100]
    # Per-user cap: 200 sessions
    existing = db.list_sessions(request.user.id, include_archived=True)
    if len(existing) >= 200:
        return jsonify({"error": "session limit reached (200). Delete some first."}), 400
    s = db.create_session(request.user.id, provider, model)
    return jsonify(s.to_dict())


@app.route("/api/sessions/<sid>", methods=["PATCH"])
@login_required
@rate_limited("default")
def sessions_update(sid):
    s = db.get_session_for_user(sid, request.user.id)
    if not s:
        return jsonify({"error": "not found"}), 404
    data = request.get_json(force=True) or {}
    if "title" in data:
        title = (str(data["title"]) if data["title"] is not None else "")[:200]
        if not db.rename_session(sid, title):
            return jsonify({"error": "not found"}), 404
    if "pinned" in data:
        if not db.set_pinned(sid, bool(data["pinned"])):
            return jsonify({"error": "not found"}), 404
    if "archived" in data:
        if not db.set_archived(sid, bool(data["archived"])):
            return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/sessions/<sid>", methods=["DELETE"])
@login_required
@rate_limited("default")
def sessions_delete(sid):
    s = db.get_session_for_user(sid, request.user.id)
    if not s:
        return jsonify({"error": "not found"}), 404
    if not db.delete_session(sid):
        return jsonify({"error": "not found"}), 404
    _pop_stream(sid, _get_stream(sid) or threading.Event())
    return jsonify({"ok": True})


@app.route("/api/sessions/<sid>/messages", methods=["GET"])
@login_required
@rate_limited("default")
def session_messages(sid):
    s = db.get_session_for_user(sid, request.user.id)
    if not s:
        return jsonify({"error": "not found"}), 404
    return jsonify([m.to_dict() for m in db.get_messages(sid)])


@app.route("/api/sessions/<sid>/clear", methods=["POST"])
@login_required
@rate_limited("default")
def session_clear(sid):
    s = db.get_session_for_user(sid, request.user.id)
    if not s:
        return jsonify({"error": "not found"}), 404
    db.clear_messages(sid)
    return jsonify({"ok": True})


@app.route("/api/sessions/<sid>/export", methods=["GET"])
@login_required
@rate_limited("default")
def session_export(sid):
    fmt = request.args.get("format", "md").lower()
    if fmt not in ("md", "json"):
        return jsonify({"error": "format must be 'md' or 'json'"}), 400
    s = db.get_session_for_user(sid, request.user.id)
    if not s:
        return jsonify({"error": "not found"}), 404
    msgs = db.get_messages(sid)
    # Sanitize title for filename
    safe_title = re.sub(r"[^\w\-\. ]+", "_", s.title or "session")[:80] or "session"
    if fmt == "json":
        body = json.dumps({"session": s.to_dict(), "messages": [m.to_dict() for m in msgs]},
                          indent=2, ensure_ascii=False)
        return Response(body, mimetype="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{safe_title}.json"'})
    # Markdown
    out = [f"# {s.title}", "", f"**Provider:** {s.provider}  ", f"**Model:** {s.model}  ", f"**Created:** {time.ctime(s.created_at)}", ""]
    for m in msgs:
        if m.role == "system" and m.metadata.get("error"):
            continue
        if m.role == "tool":
            continue
        if m.role == "user":
            out.append(f"## Você")
            out.append("")
            out.append(m.content)
            out.append("")
        elif m.role == "assistant":
            out.append(f"## Stupidex")
            out.append("")
            if m.type == "tool_call" and m.tool_calls:
                for tc in m.tool_calls:
                    out.append(f"**Tool: `{tc.get('name', '')}`**")
                    out.append("")
                    if tc.get("arguments"):
                        out.append("```json")
                        try:
                            out.append(json.dumps(json.loads(tc["arguments"]), indent=2))
                        except Exception:
                            out.append(tc["arguments"])
                        out.append("```")
                        out.append("")
            if m.content:
                out.append(m.content)
                out.append("")
    body = "\n".join(out)
    return Response(body, mimetype="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{safe_title}.md"'})


# ============================================================
# Streaming chat (with cancel support)
# ============================================================

_STREAMS_LOCK = threading.Lock()
_STREAMS: dict[str, threading.Event] = {}


def _set_stream(sid: str, ev: threading.Event) -> None:
    with _STREAMS_LOCK:
        _STREAMS[sid] = ev


def _get_stream(sid: str) -> threading.Event | None:
    with _STREAMS_LOCK:
        return _STREAMS.get(sid)


def _pop_stream(sid: str, expected: threading.Event) -> None:
    with _STREAMS_LOCK:
        if _STREAMS.get(sid) is expected:
            _STREAMS.pop(sid, None)


@app.route("/api/sessions/<sid>/stop", methods=["POST"])
@login_required
@rate_limited("chat")
def session_stop(sid):
    ev = _get_stream(sid)
    if ev:
        ev.set()
    return jsonify({"ok": True})


@app.route("/api/sessions/<sid>/regenerate", methods=["POST"])
@login_required
@rate_limited("chat")
def session_regenerate(sid):
    s = db.get_session_for_user(sid, request.user.id)
    if not s:
        return jsonify({"error": "session not found"}), 404

    data = request.get_json(silent=True) or {}
    user_text = (data.get("message") or "").strip()
    last_user = db.get_last_user_message(sid)
    if not last_user:
        return jsonify({"error": "no user message to regenerate from"}), 400

    if not user_text:
        user_text = last_user.content

    user_api_key = request.user.api_key or data.get("api_key")
    ctx = build_context(
        provider_id=s.provider,
        api_key_override=user_api_key,
        user_id=request.user.id,
    )
    ctx.session_id = sid
    if data.get("model"):
        ctx.model = data["model"].strip()
    ctx.cancel_event = threading.Event()
    _set_stream(sid, ctx.cancel_event)
    try:
        return _stream_response(sid, user_text, ctx, regenerate_user_msg_id=last_user.id)
    finally:
        _pop_stream(sid, ctx.cancel_event)


@app.route("/api/sessions/<sid>/chat", methods=["POST"])
@login_required
@rate_limited("chat")
def session_chat(sid):
    try:
        return _session_chat_impl(sid)
    except Exception as exc:
        import traceback
        tb = traceback.format_exc(limit=10)
        # Don't leak traceback to the client in production — log only.
        app.logger.error(f"session_chat fatal: {exc}\n{tb}")
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500


def _session_chat_impl(sid: str) -> Response:
    session = db.get_session_for_user(sid, request.user.id)
    if not session:
        return jsonify({"error": "session not found"}), 404

    data = request.get_json(force=True) or {}
    user_msg = (data.get("message") or "").strip()
    if not user_msg:
        return jsonify({"error": "empty message"}), 400
    if len(user_msg) > 100_000:
        return jsonify({"error": "message too long (max 100k chars)"}), 400

    user_api_key = request.user.api_key or data.get("api_key")
    if not user_api_key and not has_api_key():
        return jsonify({
            "error": "no LLM API key configured. Set DEEPSEEK_API_KEY in the server env, "
                     "or add your own key in Settings."
        }), 503
    ctx = build_context(
        provider_id=data.get("provider") or session.provider,
        api_key_override=user_api_key,
        user_id=request.user.id,
    )
    ctx.session_id = sid
    if data.get("model"):
        ctx.model = data["model"].strip()
    ctx.cancel_event = threading.Event()
    _set_stream(sid, ctx.cancel_event)
    try:
        return _stream_response(sid, user_msg, ctx)
    finally:
        _pop_stream(sid, ctx.cancel_event)


def _stream_response(sid: str, user_text: str, ctx, regenerate_user_msg_id: int | None = None) -> Response:
    q: queue.Queue = queue.Queue()
    err_holder: dict = {"err": None}

    def producer() -> None:
        try:
            for event in stream_response(
                sid, user_text, ctx, regenerate_user_msg_id=regenerate_user_msg_id
            ):
                q.put(event)
        except Exception as exc:
            import traceback as _tb
            err_holder["err"] = f"{type(exc).__name__}: {exc}"
            err_holder["trace"] = _tb.format_exc(limit=5)
        finally:
            q.put(None)

    threading.Thread(target=producer, daemon=True).start()

    def event_stream() -> Generator[str, None, None]:
        try:
            while True:
                event = q.get()
                if event is None:
                    if err_holder["err"]:
                        payload = {"type": "error", "content": err_holder["err"]}
                        if "trace" in err_holder:
                            payload["trace"] = err_holder["trace"]
                        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except GeneratorExit:
            ctx.cancel_event.set()
            raise

    return Response(event_stream(), mimetype="text/event-stream",
                    headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


# ============================================================
# Workspaces (per-user scoped)
# ============================================================

def get_user_workspace_dir(user_id: str) -> Path:
    return DATA_DIR / "workspaces" / user_id


# Allowed hostnames for git clone (SSRF mitigation).
# Empty set = allow only direct .git urls to known git hosts. Users can add
# more via STUPIDEX_GIT_HOSTS env var (comma-separated).
_GIT_HOST_ALLOWLIST = set(filter(None, [
    "github.com", "www.github.com",
    "gitlab.com", "www.gitlab.com",
    "bitbucket.org", "www.bitbucket.org",
    "codeberg.org",
] + [h.strip().lower() for h in os.environ.get("STUPIDEX_GIT_HOSTS", "").split(",") if h.strip()]))


def _validate_git_url(url: str) -> str | None:
    """Return an error string if the URL is not safe to clone, else None."""
    if not url:
        return "url is required"
    if len(url) > 2048:
        return "url too long"
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("https", "git", "ssh"):
        return f"unsupported URL scheme: {parsed.scheme!r} (use https, git, or ssh)"
    if parsed.scheme in ("https", "git") and parsed.hostname:
        host = parsed.hostname.lower()
        if host not in _GIT_HOST_ALLOWLIST:
            return f"host {host!r} not in git allowlist"
    # Disallow userinfo (e.g. https://user:pass@host/...)
    if "@" in parsed.netloc:
        return "URLs with credentials are not allowed"
    return None


@app.route("/api/workspaces", methods=["GET"])
@login_required
@rate_limited("default")
def workspaces_list():
    ws_list = [w.to_dict() for w in workspaces_module.list_workspaces(request.user.id)]
    active = workspaces_module.get_active_workspace(request.user.id)
    return jsonify({
        "workspaces": ws_list,
        "active_id": active.id if active else None,
    })


@app.route("/api/workspaces", methods=["POST"])
@login_required
@rate_limited("default")
def workspaces_create():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip() or "Workspace"
    if len(name) > 100:
        return jsonify({"error": "name too long (max 100 chars)"}), 400
    # Per-user cap: 50 workspaces
    existing = workspaces_module.list_workspaces(request.user.id)
    if len(existing) >= 50:
        return jsonify({"error": "workspace limit reached (50). Delete some first."}), 400
    ws = workspaces_module.create_empty(request.user.id, name)
    if not workspaces_module.get_active_workspace(request.user.id):
        workspaces_module.set_active_workspace(request.user.id, ws.id)
    return jsonify({"ok": True, "workspace": ws.to_dict()})


@app.route("/api/workspaces/<ws_id>", methods=["DELETE"])
@login_required
@rate_limited("default")
def workspaces_delete(ws_id):
    if not workspaces_module.delete_workspace(request.user.id, ws_id):
        return jsonify({"error": "workspace not found"}), 404
    active = workspaces_module.get_active_workspace(request.user.id)
    return jsonify({"ok": True, "active_id": active.id if active else None})


@app.route("/api/workspaces/<ws_id>/activate", methods=["POST"])
@login_required
@rate_limited("default")
def workspaces_activate(ws_id):
    if not workspaces_module.set_active_workspace(request.user.id, ws_id):
        return jsonify({"error": "workspace not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/workspaces/<ws_id>/upload", methods=["POST"])
@login_required
@rate_limited("upload")
def workspaces_upload(ws_id):
    ws_path = workspaces_module.workspace_path(request.user.id, ws_id)
    if not ws_path:
        return jsonify({"error": "workspace not found"}), 404

    rel_path = request.form.get("path", "").strip().lstrip("/\\")
    target = (ws_path / rel_path).resolve() if rel_path else ws_path.resolve()
    if not str(target).startswith(str(ws_path.resolve())):
        return jsonify({"error": "invalid path"}), 400
    target.mkdir(parents=True, exist_ok=True)

    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "no files in upload"}), 400

    saved = []
    for f in files:
        rel = (f.filename or "").replace("\\", "/").lstrip("/")
        if not rel or rel.endswith("/"):
            continue
        # Reject traversal segments and NUL bytes
        if ".." in Path(rel).parts or "\x00" in rel:
            continue
        dest = (target / rel).resolve()
        # Final containment check (handles symlinks too via resolve())
        if not str(dest).startswith(str(ws_path.resolve())):
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        f.save(str(dest))
        saved.append(rel)

    workspaces_module.touch(request.user.id, ws_id)
    workspaces_module.init_from_upload(request.user.id, ws_id)
    ws = workspaces_module.get_workspace(request.user.id, ws_id)
    return jsonify({"ok": True, "saved": saved, "workspace": ws.to_dict()})


@app.route("/api/workspaces/<ws_id>/upload-zip", methods=["POST"])
@login_required
@rate_limited("upload")
def workspaces_upload_zip(ws_id):
    ws_path = workspaces_module.workspace_path(request.user.id, ws_id)
    if not ws_path:
        return jsonify({"error": "workspace not found"}), 404
    if any(ws_path.iterdir()):
        return jsonify({"error": "workspace is not empty — delete it first or use a fresh one"}), 400

    f = request.files.get("file")
    if not f:
        return jsonify({"error": "no file uploaded"}), 400
    data = f.read()
    if len(data) > 50 * 1024 * 1024:
        return jsonify({"error": "zip too large (max 50 MB)"}), 413
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for member in zf.namelist():
                if member.endswith("/"):
                    continue
                if ".." in Path(member).parts or "\x00" in member:
                    continue
                target = (ws_path / member).resolve()
                if not str(target).startswith(str(ws_path.resolve())):
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(target, "wb") as dst:
                    dst.write(src.read())
    except zipfile.BadZipFile:
        return jsonify({"error": "not a valid .zip file"}), 400

    workspaces_module.touch(request.user.id, ws_id)
    workspaces_module.init_from_upload(request.user.id, ws_id)
    ws = workspaces_module.get_workspace(request.user.id, ws_id)
    return jsonify({"ok": True, "workspace": ws.to_dict()})


@app.route("/api/workspaces/<ws_id>/clone", methods=["POST"])
@login_required
@rate_limited("upload")
def workspaces_clone(ws_id):
    data = request.get_json(force=True) or {}
    url = (data.get("url") or "").strip()
    branch = (data.get("branch") or "").strip() or None
    err = _validate_git_url(url)
    if err:
        return jsonify({"error": err}), 400
    try:
        ws, stderr = workspaces_module.init_from_git(request.user.id, ws_id, url, branch)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500
    except Exception as exc:
        return jsonify({"error": f"clone failed: {exc}"}), 500
    return jsonify({"ok": True, "workspace": ws.to_dict(), "stderr": stderr})


@app.route("/api/workspaces/<ws_id>/pull", methods=["POST"])
@login_required
@rate_limited("upload")
def workspaces_pull(ws_id):
    ok, output = workspaces_module.git_pull(request.user.id, ws_id)
    return jsonify({"ok": ok, "output": output})


@app.route("/api/workspaces/<ws_id>/tree", methods=["GET"])
@login_required
@rate_limited("default")
def workspaces_tree(ws_id):
    return jsonify({"tree": workspaces_module.file_tree(request.user.id, ws_id)})


@app.route("/api/workspaces/<ws_id>/file", methods=["GET"])
@login_required
@rate_limited("default")
def workspaces_file(ws_id):
    ws_path = workspaces_module.workspace_path(request.user.id, ws_id)
    if not ws_path:
        return jsonify({"error": "workspace not found"}), 404
    rel = (request.args.get("path") or "").strip().lstrip("/\\")
    if not rel:
        return jsonify({"error": "path required"}), 400
    if ".." in Path(rel).parts or "\x00" in rel:
        return jsonify({"error": "invalid path"}), 400
    target = (ws_path / rel).resolve()
    if not str(target).startswith(str(ws_path.resolve())):
        return jsonify({"error": "invalid path"}), 400
    if not target.is_file():
        return jsonify({"error": "not a file"}), 404
    # Cap file size to avoid OOM
    if target.stat().st_size > 1 * 1024 * 1024:
        return jsonify({"error": "file too large (max 1 MB)"}), 413
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return jsonify({"error": "binary file"}), 415
    return jsonify({
        "path": rel,
        "content": content,
        "size": target.stat().st_size,
    })


# ============================================================
# Entry point
# ============================================================

def main():
    import os as _os
    host = _os.environ.get("STUPIDEX_HOST", "0.0.0.0")
    port = int(_os.environ.get("STUPIDEX_PORT", _os.environ.get("PORT", "5000")))
    debug = _os.environ.get("STUPIDEX_DEBUG") == "1"
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    main()
