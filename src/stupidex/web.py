"""Stupidex web server — Flask + SSE streaming, with auth, CORS, rate limit.

Endpoints:
  GET  /                                  → SPA
  GET  /api/health                        → liveness probe

  POST /api/auth/register                 → {username, password} → {user, token}
  POST /api/auth/login                    → {username, password} → {user, token}
  POST /api/auth/logout                   → invalidate token (login_required)
  GET  /api/auth/me                       → current user info (login_required)
  GET  /api/integrations/github           → GitHub connection status
  GET  /api/integrations/github/connect   → start GitHub OAuth connection
  DELETE /api/integrations/github         → disconnect GitHub account

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

import base64
import binascii
import json
import os
import queue
import re
import secrets
import shutil
import tempfile
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

from . import db
from . import workspaces as workspaces_module
from .config import DATA_DIR, has_api_key, load_config
from .llm.handle_input import build_context, stream_response
from .llm.providers import DEFAULT_FALLBACK_ID, PROVIDERS, list_providers

app = Flask(__name__, static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = (
    50 * 1024 * 1024
)  # 50 MB upload limit (DoS hardening)

MAX_CHAT_IMAGES = 4
MAX_CHAT_IMAGE_BYTES = 5 * 1024 * 1024
_CHAT_IMAGE_MIMES = {
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/webp": b"RIFF",
}


def _validate_chat_images(raw_images) -> tuple[list[dict], str | None]:
    if raw_images is None:
        return [], None
    if not isinstance(raw_images, list):
        return [], "images must be a list"
    if len(raw_images) > MAX_CHAT_IMAGES:
        return [], f"too many images (max {MAX_CHAT_IMAGES})"

    normalized: list[dict] = []
    for index, raw in enumerate(raw_images):
        if not isinstance(raw, dict):
            return [], f"image {index + 1} is invalid"
        data_url = raw.get("data_url")
        if not isinstance(data_url, str) or not data_url.startswith("data:image/"):
            return [], f"image {index + 1} must use a data URL"
        try:
            header, encoded = data_url.split(",", 1)
            mime = header[5:].split(";", 1)[0].lower()
        except ValueError:
            return [], f"image {index + 1} has an invalid data URL"
        if ";base64" not in header.lower() or mime not in _CHAT_IMAGE_MIMES:
            return [], f"unsupported image type: {mime or 'unknown'}"
        if len(encoded) > ((MAX_CHAT_IMAGE_BYTES + 2) // 3) * 4 + 8:
            return [], f"image {index + 1} is too large"
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            return [], f"image {index + 1} has invalid base64 data"
        if not content or len(content) > MAX_CHAT_IMAGE_BYTES:
            return [], f"image {index + 1} is too large"
        signature = _CHAT_IMAGE_MIMES[mime]
        signatures = signature if isinstance(signature, tuple) else (signature,)
        valid_signature = any(content.startswith(item) for item in signatures)
        if mime == "image/webp":
            valid_signature = (
                valid_signature and len(content) >= 12 and content[8:12] == b"WEBP"
            )
        if not valid_signature:
            return [], f"image {index + 1} content does not match {mime}"
        name = re.sub(
            r"[^A-Za-z0-9._ -]+", "_", str(raw.get("name") or f"image-{index + 1}")
        )[:120]
        normalized.append(
            {"data_url": data_url, "mime": mime, "name": name, "size": len(content)}
        )
    return normalized, None


# CORS: when STUPIDEX_CORS is unset, allow only same-origin (no header).
# Set to a comma-separated list of origins or "*" to allow any.
_cors_env = os.environ.get("STUPIDEX_CORS", "").strip()
CORS_ORIGINS = (
    [o.strip() for o in _cors_env.split(",") if o.strip()] if _cors_env else []
)


@app.before_request
def _enforce_browser_origin():
    if request.method not in {"POST", "PATCH", "PUT", "DELETE"}:
        return None
    origin = request.headers.get("Origin", "").rstrip("/")
    if not origin:
        return None
    parsed = urllib.parse.urlparse(origin)
    same_origin = parsed.netloc.lower() == request.host.lower()
    explicitly_allowed = origin in {
        item.rstrip("/") for item in CORS_ORIGINS if item != "*"
    }
    if not same_origin and not explicitly_allowed:
        return jsonify({"error": "origin not allowed"}), 403
    return None


# Google OAuth
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.environ.get(
    "GOOGLE_REDIRECT_URI",
    (
        os.environ.get("FRONTEND_URL")
        or os.environ.get("RENDER_EXTERNAL_URL")
        or "http://localhost:" + os.environ.get("PORT", "5000")
    )
    + "/api/auth/google/callback",
)
_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

# GitHub OAuth App integration. The `repo` scope is required to download
# archives from private repositories selected by the connected user.
GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "")
GITHUB_REDIRECT_URI = os.environ.get(
    "GITHUB_REDIRECT_URI",
    (
        os.environ.get("FRONTEND_URL")
        or os.environ.get("RENDER_EXTERNAL_URL")
        or "http://localhost:" + os.environ.get("PORT", "5000")
    )
    + "/api/integrations/github/callback",
)
_GITHUB_AUTH_URL = "https://github.com/login/oauth/authorize"
_GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
_GITHUB_USER_URL = "https://api.github.com/user"

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
    ("auth", 10, 60.0),  # /api/auth/*   — login/register/logout
    ("chat", 60, 60.0),  # /chat, /regenerate, /stop
    ("upload", 20, 60.0),  # upload, clone
    ("default", 240, 60.0),  # everything else
]


def _rate_limit_check(bucket: str, identity: str) -> bool:
    """Returns True if the request is allowed, False if it should be 429'd."""
    rule = next((r for r in _RL_RULES if r[0] == bucket), _RL_RULES[-1])
    name, max_req, window = rule
    key = f"{name}:{identity}"
    now = time.time()
    with _RL_LOCK:
        if len(_RL_BUCKETS) > 10_000:
            cutoff = now - max(rule[2] for rule in _RL_RULES)
            stale = [
                k
                for k, values in _RL_BUCKETS.items()
                if not values or values[-1] < cutoff
            ]
            for stale_key in stale:
                _RL_BUCKETS.pop(stale_key, None)
        if key not in _RL_BUCKETS and len(_RL_BUCKETS) >= 20_000:
            return False
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
        token = _request_token()
        if token:
            u = db.validate_token(token)
            if u:
                return f"u:{u.id}"
    except Exception:
        pass
    return f"ip:{request.remote_addr or 'unknown'}"


# OAuth `state` cookie store (CSRF defense)
_OAUTH_STATE_COOKIE = "stupidex_oauth_state"
_GITHUB_STATE_COOKIE = "stupidex_github_oauth_state"
_AUTH_COOKIE = "stupidex_auth"


def _request_token() -> str:
    raw = request.headers.get("Authorization", "")
    bearer = raw.removeprefix("Bearer ").strip()
    return bearer or request.cookies.get(_AUTH_COOKIE, "").strip()


def _set_auth_cookie(resp: Response, token: str) -> Response:
    resp.set_cookie(
        _AUTH_COOKIE,
        token,
        max_age=86400 * 30,
        httponly=True,
        secure=request.is_secure or request.headers.get("X-Forwarded-Proto") == "https",
        samesite="Lax",
        path="/",
    )
    return resp


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
        token = _request_token()
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
        resp.headers["Access-Control-Allow-Methods"] = (
            "GET, POST, PATCH, DELETE, OPTIONS"
        )
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
    return jsonify(
        {
            "ok": True,
            "ts": time.time(),
            "v": "oauth-fix-v3",
            "integrations": {
                "github_configured": _github_configured(),
                "google_configured": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),
            },
        }
    )


# ============================================================
# Google OAuth
# ============================================================


@app.route("/api/auth/google", methods=["GET"])
@rate_limited("auth")
def auth_google():
    if not GOOGLE_CLIENT_ID:
        return jsonify(
            {
                "error": "Google OAuth not configured (set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)"
            }
        ), 501

    state = secrets.token_urlsafe(24)
    # CSRF: bind the state to a signed cookie. Callback must match.
    nonce = secrets.token_urlsafe(16)
    params = urllib.parse.urlencode(
        {
            "client_id": GOOGLE_CLIENT_ID,
            "redirect_uri": GOOGLE_REDIRECT_URI,
            "response_type": "code",
            "scope": "openid email profile",
            "state": f"{state}.{nonce}",
            "access_type": "offline",
            "prompt": "select_account",
        }
    )
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
    if (
        not state_qs
        or not state_cookie
        or not secrets.compare_digest(state_qs, state_cookie)
    ):
        return jsonify({"error": "invalid OAuth state (possible CSRF)"}), 400

    # Exchange code for access token
    try:
        token_req = urllib.request.Request(
            _GOOGLE_TOKEN_URL,
            data=urllib.parse.urlencode(
                {
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": GOOGLE_REDIRECT_URI,
                }
            ).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(token_req, timeout=10) as resp:
            token_data = json.loads(resp.read())
        access_token = token_data.get("access_token")
        if not access_token:
            return jsonify(
                {
                    "error": "failed to exchange code",
                    "detail": token_data.get("error_description", ""),
                }
            ), 400
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

    frontend = os.environ.get("FRONTEND_URL", "/").strip() or "/"
    if frontend != "/":
        parsed_frontend = urllib.parse.urlparse(frontend)
        if (
            parsed_frontend.scheme not in ("http", "https")
            or not parsed_frontend.netloc
        ):
            frontend = "/"
    resp = redirect(frontend)
    _set_auth_cookie(resp, token)
    resp.set_cookie(_OAUTH_STATE_COOKIE, "", max_age=0, path="/")
    return resp


# ============================================================
# GitHub integration
# ============================================================


def _github_configured() -> bool:
    return bool(GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET)


def _frontend_redirect_url(github_status: str) -> str:
    frontend = os.environ.get("FRONTEND_URL", "/").strip() or "/"
    if frontend != "/":
        parsed = urllib.parse.urlparse(frontend)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            frontend = "/"
    parsed = urllib.parse.urlparse(frontend)
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    query["github"] = github_status
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))


@app.route("/api/integrations/github", methods=["GET"])
@login_required
@rate_limited("default")
def github_integration_status():
    return jsonify(
        {
            "configured": _github_configured(),
            "connected": bool(request.user.github_access_token),
            "login": request.user.github_login,
            "avatar_url": request.user.github_avatar_url,
            "connected_at": request.user.github_connected_at,
            "scope": "repo",
        }
    )


@app.route("/api/integrations/github/connect", methods=["GET"])
@login_required
@rate_limited("auth")
def github_integration_connect():
    if not _github_configured():
        return jsonify(
            {
                "error": "GitHub OAuth is not configured",
                "detail": "Server administrator must set GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, and GITHUB_REDIRECT_URI environment variables. See README.md for setup instructions.",
            }
        ), 501

    state = secrets.token_urlsafe(32)
    params = urllib.parse.urlencode(
        {
            "client_id": GITHUB_CLIENT_ID,
            "redirect_uri": GITHUB_REDIRECT_URI,
            "scope": "repo",
            "state": state,
            "allow_signup": "false",
        }
    )
    resp = redirect(f"{_GITHUB_AUTH_URL}?{params}")
    resp.set_cookie(
        _GITHUB_STATE_COOKIE,
        state,
        max_age=600,
        httponly=True,
        secure=request.is_secure or request.headers.get("X-Forwarded-Proto") == "https",
        samesite="Lax",
        path="/",
    )
    return resp


@app.route("/api/integrations/github/callback", methods=["GET"])
@login_required
@rate_limited("auth")
def github_integration_callback():
    state_qs = request.args.get("state", "")
    state_cookie = request.cookies.get(_GITHUB_STATE_COOKIE, "")
    if (
        not state_qs
        or not state_cookie
        or not secrets.compare_digest(state_qs, state_cookie)
    ):
        return jsonify({"error": "invalid OAuth state (possible CSRF)"}), 400

    if request.args.get("error"):
        resp = redirect(_frontend_redirect_url("denied"))
        resp.set_cookie(_GITHUB_STATE_COOKIE, "", max_age=0, path="/")
        return resp
    code = request.args.get("code", "")
    if not code:
        return jsonify({"error": "missing authorization code"}), 400
    if not _github_configured():
        return jsonify(
            {
                "error": "GitHub OAuth is not configured",
                "detail": "Server configuration changed. Please try connecting again.",
            }
        ), 501

    try:
        token_req = urllib.request.Request(
            _GITHUB_TOKEN_URL,
            data=urllib.parse.urlencode(
                {
                    "client_id": GITHUB_CLIENT_ID,
                    "client_secret": GITHUB_CLIENT_SECRET,
                    "code": code,
                    "redirect_uri": GITHUB_REDIRECT_URI,
                }
            ).encode(),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "Stupidex/0.1",
            },
        )
        with urllib.request.urlopen(token_req, timeout=10) as token_resp:
            token_data = json.loads(token_resp.read())
        access_token = (token_data.get("access_token") or "").strip()
        granted_scopes = {
            item.strip() for item in (token_data.get("scope") or "").split(",")
        }
        if not access_token:
            raise RuntimeError("GitHub did not return an access token")
        if "repo" not in granted_scopes:
            raise RuntimeError("GitHub did not grant private repository access")

        user_req = urllib.request.Request(
            _GITHUB_USER_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {access_token}",
                "User-Agent": "Stupidex/0.1",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(user_req, timeout=10) as user_resp:
            github_user = json.loads(user_resp.read())
        login = (github_user.get("login") or "").strip()
        avatar_url = (github_user.get("avatar_url") or "").strip()
        if not login:
            raise RuntimeError("GitHub did not return an account login")
        db.update_github_connection(request.user.id, access_token, login, avatar_url)
    except Exception:
        resp = redirect(_frontend_redirect_url("error"))
        resp.set_cookie(_GITHUB_STATE_COOKIE, "", max_age=0, path="/")
        return resp

    resp = redirect(_frontend_redirect_url("connected"))
    resp.set_cookie(_GITHUB_STATE_COOKIE, "", max_age=0, path="/")
    return resp


@app.route("/api/integrations/github", methods=["DELETE"])
@login_required
@rate_limited("default")
def github_integration_disconnect():
    db.clear_github_connection(request.user.id)
    return jsonify({"ok": True})


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
    return _set_auth_cookie(jsonify({"user": user.to_dict(), "token": token}), token)


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
    return _set_auth_cookie(jsonify({"user": user.to_dict(), "token": token}), token)


@app.route("/api/auth/enter", methods=["POST"])
@rate_limited("auth")
def auth_enter():
    """Authenticate an existing account or create a new one with one request."""
    data = request.get_json(force=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    try:
        user, token = db.authenticate_user(username, password)
    except ValueError as login_error:
        if "too many" in str(login_error).lower():
            return jsonify({"error": str(login_error)}), 429
        try:
            user, token = db.create_user(username, password)
        except ValueError as create_error:
            if "already taken" in str(create_error).lower():
                return jsonify({"error": "invalid username or password"}), 401
            return jsonify({"error": str(create_error)}), 400
    return _set_auth_cookie(jsonify({"user": user.to_dict(), "token": token}), token)


@app.route("/api/auth/logout", methods=["POST"])
@login_required
@rate_limited("auth")
def auth_logout():
    token = _request_token()
    db.logout_token(token)
    resp = jsonify({"ok": True})
    resp.delete_cookie(_AUTH_COOKIE, path="/")
    return resp


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
    provider = request.user.provider or cfg.provider
    if provider not in PROVIDERS:
        provider = DEFAULT_FALLBACK_ID
    custom_model = request.user.custom_model or ""
    model = custom_model or request.user.model or PROVIDERS[provider].default_model
    return jsonify(
        {
            "provider": provider,
            "model": model,
            "custom_model": custom_model,
            "has_api_key": bool(request.user.api_key or cfg.api_key),
        }
    )


@app.route("/api/config", methods=["POST"])
@login_required
def set_config():
    data = request.get_json(force=True) or {}
    provider = (data.get("provider") or "").strip() or None
    custom_model = str(data.get("custom_model") or "").strip()
    api_key = data.get("api_key", None)
    if provider and provider not in PROVIDERS:
        return jsonify({"error": f"unknown provider: {provider}"}), 400
    provider = provider or request.user.provider or load_config().provider
    if provider not in PROVIDERS:
        provider = DEFAULT_FALLBACK_ID
    model = custom_model or PROVIDERS[provider].default_model
    if len(custom_model) > 200:
        return jsonify({"error": "model name too long"}), 400
    db.update_user_config(
        request.user.id,
        provider=provider,
        model=model,
        custom_model=custom_model,
        api_key=str(api_key).strip() if api_key else None,
        clear_api_key=bool(data.get("clear_api_key")),
    )
    server_key = load_config().api_key
    has_key = bool(
        api_key
        or (request.user.api_key and not data.get("clear_api_key"))
        or server_key
    )
    return jsonify(
        {
            "ok": True,
            "has_api_key": has_key,
            "provider": provider,
            "model": model,
            "custom_model": custom_model,
        }
    )


# ============================================================
# Sessions
# ============================================================


@app.route("/api/sessions", methods=["GET"])
@login_required
@rate_limited("default")
def sessions_list():
    include_archived = request.args.get("include_archived") == "1"
    include_trashed = request.args.get("include_trashed") == "1"
    only_trashed = request.args.get("trashed") == "1"
    return jsonify(
        [
            s.to_dict()
            for s in db.list_sessions(
                request.user.id,
                include_archived=include_archived,
                include_trashed=include_trashed or only_trashed,
                only_trashed=only_trashed,
            )
        ]
    )


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
    provider = data.get("provider") or request.user.provider or cfg.provider
    if provider not in PROVIDERS:
        provider = DEFAULT_FALLBACK_ID
    model = (
        data.get("model")
        or request.user.custom_model
        or request.user.model
        or PROVIDERS[provider].default_model
    ).strip()[:200]
    # Per-user cap: 200 sessions
    existing = db.list_sessions(
        request.user.id, include_archived=True, include_trashed=True
    )
    if len(existing) >= 200:
        return jsonify(
            {"error": "session limit reached (200). Delete some first."}
        ), 400
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
    if "trashed" in data:
        if not db.set_trashed(sid, bool(data["trashed"])):
            return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/sessions/<sid>", methods=["DELETE"])
@login_required
@rate_limited("default")
def sessions_delete(sid):
    s = db.get_session_for_user(sid, request.user.id)
    if not s:
        return jsonify({"error": "not found"}), 404
    if not s.trashed:
        return jsonify({"error": "move the session to trash before deleting it"}), 409
    ev = _get_stream(sid)
    if ev:
        ev.set()
    if not db.delete_session(sid):
        return jsonify({"error": "not found"}), 404
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
    if _get_stream(sid):
        return jsonify({"error": "session is currently generating"}), 409
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
        body = json.dumps(
            {"session": s.to_dict(), "messages": [m.to_dict() for m in msgs]},
            indent=2,
            ensure_ascii=False,
        )
        return Response(
            body,
            mimetype="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{safe_title}.json"'
            },
        )
    # Markdown
    out = [
        f"# {s.title}",
        "",
        f"**Provider:** {s.provider}  ",
        f"**Model:** {s.model}  ",
        f"**Created:** {time.ctime(s.created_at)}",
        "",
    ]
    for m in msgs:
        if m.role == "system" and m.metadata.get("error"):
            continue
        if m.role == "tool":
            continue
        if m.role == "user":
            out.append("## Você")
            out.append("")
            out.append(m.content)
            out.append("")
        elif m.role == "assistant":
            out.append("## Stupidex")
            out.append("")
            if m.type == "tool_call" and m.tool_calls:
                for tc in m.tool_calls:
                    out.append(f"**Tool: `{tc.get('name', '')}`**")
                    out.append("")
                    if tc.get("arguments"):
                        out.append("```json")
                        try:
                            out.append(
                                json.dumps(json.loads(tc["arguments"]), indent=2)
                            )
                        except Exception:
                            out.append(tc["arguments"])
                        out.append("```")
                        out.append("")
            if m.content:
                out.append(m.content)
                out.append("")
    body = "\n".join(out)
    return Response(
        body,
        mimetype="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe_title}.md"'},
    )


# ============================================================
# Streaming chat (with cancel support)
# ============================================================

_STREAMS_LOCK = threading.Lock()
_STREAMS: dict[str, threading.Event] = {}


def _claim_stream(sid: str) -> threading.Event | None:
    with _STREAMS_LOCK:
        current = _STREAMS.get(sid)
        if current is not None:
            return None
        ev = threading.Event()
        _STREAMS[sid] = ev
        return ev


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
    if not db.get_session_for_user(sid, request.user.id):
        return jsonify({"error": "session not found"}), 404
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
    if s.trashed:
        return jsonify({"error": "session is in trash"}), 409

    data = request.get_json(silent=True) or {}
    user_text = (data.get("message") or "").strip()
    last_user = db.get_last_user_message(sid)
    if not last_user:
        return jsonify({"error": "no user message to regenerate from"}), 400

    if not user_text:
        user_text = last_user.content

    provider_id = data.get("provider") or s.provider
    user_api_key = request.user.api_key or data.get("api_key")
    if (
        PROVIDERS.get(provider_id, PROVIDERS[DEFAULT_FALLBACK_ID]).needs_api_key
        and not user_api_key
        and not has_api_key()
    ):
        return jsonify({"error": "no LLM API key configured"}), 503
    ctx = build_context(
        provider_id=provider_id,
        api_key_override=user_api_key,
        user_id=request.user.id,
        model_override=(data.get("model") or s.model),
        github_token=request.user.github_access_token,
    )
    ctx.web_search_enabled = bool(last_user.metadata.get("web_search"))
    ctx.session_id = sid
    ctx.cancel_event = _claim_stream(sid)
    if ctx.cancel_event is None:
        return jsonify({"error": "session is already generating"}), 409
    return _stream_response(sid, user_text, ctx, regenerate_user_msg_id=last_user.id)


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
        error_id = secrets.token_hex(6)
        app.logger.error("session_chat fatal id=%s: %s\n%s", error_id, exc, tb)
        return jsonify({"error": "internal server error", "error_id": error_id}), 500


def _session_chat_impl(sid: str) -> Response:
    session = db.get_session_for_user(sid, request.user.id)
    if not session:
        return jsonify({"error": "session not found"}), 404
    if session.trashed:
        return jsonify({"error": "session is in trash"}), 409

    data = request.get_json(force=True) or {}
    user_msg = (data.get("message") or "").strip()
    images, image_error = _validate_chat_images(data.get("images"))
    if image_error:
        return jsonify({"error": image_error}), 400
    if not user_msg and not images:
        return jsonify({"error": "empty message"}), 400
    if len(user_msg) > 100_000:
        return jsonify({"error": "message too long (max 100k chars)"}), 400

    provider_id = data.get("provider") or session.provider
    provider = PROVIDERS.get(provider_id, PROVIDERS[DEFAULT_FALLBACK_ID])
    if images and not provider.supports_vision:
        return jsonify(
            {"error": "the selected model does not support image input"}
        ), 400
    user_api_key = request.user.api_key or data.get("api_key")
    if provider.needs_api_key and not user_api_key and not has_api_key():
        return jsonify(
            {
                "error": "no LLM API key configured. Set DEEPSEEK_API_KEY in the server env, "
                "or add your own key in Settings."
            }
        ), 503
    ctx = build_context(
        provider_id=provider_id,
        api_key_override=user_api_key,
        user_id=request.user.id,
        model_override=(data.get("model") or session.model),
        github_token=request.user.github_access_token,
    )
    ctx.web_search_enabled = data.get("web_search") is True
    ctx.session_id = sid
    ctx.cancel_event = _claim_stream(sid)
    if ctx.cancel_event is None:
        return jsonify({"error": "session is already generating"}), 409
    return _stream_response(sid, user_msg, ctx, images=images)


def _stream_response(
    sid: str,
    user_text: str,
    ctx,
    regenerate_user_msg_id: int | None = None,
    images: list[dict] | None = None,
) -> Response:
    q: queue.Queue = queue.Queue(maxsize=128)
    err_holder: dict = {"err": None}
    producer_done = threading.Event()

    def producer() -> None:
        try:
            for event in stream_response(
                sid,
                user_text,
                ctx,
                regenerate_user_msg_id=regenerate_user_msg_id,
                images=images,
            ):
                while not ctx.cancel_event.is_set():
                    try:
                        q.put(event, timeout=0.25)
                        break
                    except queue.Full:
                        continue
        except Exception as exc:
            import traceback as _tb

            error_id = secrets.token_hex(6)
            err_holder["err"] = "internal server error"
            err_holder["error_id"] = error_id
            app.logger.error(
                "stream fatal id=%s sid=%s: %s\n%s",
                error_id,
                sid,
                exc,
                _tb.format_exc(limit=5),
            )
        finally:
            _pop_stream(sid, ctx.cancel_event)
            producer_done.set()

    threading.Thread(target=producer, daemon=True).start()

    def event_stream() -> Generator[str, None, None]:
        try:
            while True:
                try:
                    event = q.get(timeout=0.5)
                except queue.Empty:
                    if producer_done.is_set():
                        event = None
                    else:
                        continue
                if event is None:
                    if err_holder["err"]:
                        payload = {"type": "error", "content": err_holder["err"]}
                        payload["error_id"] = err_holder.get("error_id")
                        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except GeneratorExit:
            ctx.cancel_event.set()
            raise

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


# ============================================================
# Workspaces (per-user scoped)
# ============================================================


def get_user_workspace_dir(user_id: str) -> Path:
    return DATA_DIR / "workspaces" / user_id


# Allowed hostnames for archive-based repository cloning (SSRF mitigation).
_GIT_HOST_ALLOWLIST = set(
    filter(
        None,
        [
            "github.com",
            "www.github.com",
            "gitlab.com",
            "www.gitlab.com",
        ],
    )
)


def _validate_git_url(url: str) -> str | None:
    """Return an error string if the URL is not safe to clone, else None."""
    if not url:
        return "url is required"
    if len(url) > 2048:
        return "url too long"
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        return "only HTTPS repository URLs are allowed"
    host = (parsed.hostname or "").lower()
    if host not in _GIT_HOST_ALLOWLIST:
        return f"host {host!r} not in git allowlist"
    try:
        port = parsed.port
    except ValueError:
        return "invalid repository port"
    if port not in (None, 443):
        return "custom repository ports are not allowed"
    # Disallow userinfo (e.g. https://user:pass@host/...)
    if "@" in parsed.netloc:
        return "URLs with credentials are not allowed"
    if parsed.query or parsed.fragment:
        return "repository URL cannot contain a query or fragment"
    if host in {"github.com", "www.github.com"}:
        parts = [part for part in parsed.path.strip("/").split("/") if part]
        if len(parts) != 2 or not parts[1].removesuffix(".git"):
            return "GitHub URL must identify one repository"
    return None


def _path_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, RuntimeError, ValueError):
        return False


@app.route("/api/workspaces", methods=["GET"])
@login_required
@rate_limited("default")
def workspaces_list():
    ws_list = [w.to_dict() for w in workspaces_module.list_workspaces(request.user.id)]
    active = workspaces_module.get_active_workspace(request.user.id)
    return jsonify(
        {
            "workspaces": ws_list,
            "active_id": active.id if active else None,
        }
    )


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
        return jsonify(
            {"error": "workspace limit reached (50). Delete some first."}
        ), 400
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
    if not _path_within(target, ws_path):
        return jsonify({"error": "invalid path"}), 400
    target.mkdir(parents=True, exist_ok=True)

    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "no files in upload"}), 400

    current = workspaces_module.get_workspace(request.user.id, ws_id)
    total_bytes = current.size_bytes if current else 0
    total_files = current.file_count if current else 0
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
        if not _path_within(dest, ws_path):
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        existed = dest.is_file()
        old_size = dest.stat().st_size if existed else 0
        projected_files = total_files if existed else total_files + 1
        if projected_files > workspaces_module.MAX_WORKSPACE_FILES:
            return jsonify({"error": "workspace file limit exceeded"}), 413
        written = 0
        temp_upload: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as out:
                temp_upload = Path(out.name)
                while True:
                    chunk = f.stream.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > workspaces_module.MAX_FILE_BYTES:
                        raise ValueError("file too large")
                    if (
                        total_bytes - old_size + written
                        > workspaces_module.MAX_WORKSPACE_BYTES
                    ):
                        raise ValueError("workspace storage limit exceeded")
                    out.write(chunk)
            temp_upload.replace(dest)
        except ValueError as exc:
            if temp_upload is not None:
                temp_upload.unlink(missing_ok=True)
            return jsonify({"error": str(exc)}), 413
        total_bytes = total_bytes - old_size + written
        total_files = projected_files
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
    if any(child.name != ".stupidex.json" for child in ws_path.iterdir()):
        return jsonify(
            {"error": "workspace is not empty — delete it first or use a fresh one"}
        ), 400

    f = request.files.get("file")
    if not f:
        return jsonify({"error": "no file uploaded"}), 400
    tmp_path: Path | None = None
    stage: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            total = 0
            while True:
                chunk = f.stream.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > workspaces_module.MAX_ARCHIVE_BYTES:
                    return jsonify({"error": "zip too large (max 50 MB)"}), 413
                tmp.write(chunk)
        stage = Path(tempfile.mkdtemp(prefix=f".{ws_id}-upload-", dir=ws_path.parent))
        workspaces_module._extract_archive(tmp_path, stage)
        for child in stage.iterdir():
            shutil.move(str(child), str(ws_path / child.name))
    except zipfile.BadZipFile:
        return jsonify({"error": "not a valid .zip file"}), 400
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 413
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)

    workspaces_module.touch(request.user.id, ws_id)
    workspaces_module.init_from_upload(request.user.id, ws_id)
    ws = workspaces_module.get_workspace(request.user.id, ws_id)
    return jsonify({"ok": True, "workspace": ws.to_dict()})


@app.route("/api/workspaces/<ws_id>/clone", methods=["POST"])
@login_required
@rate_limited("upload")
def workspaces_clone(ws_id):
    data = request.get_json(force=True) or {}
    ws_path = workspaces_module.workspace_path(request.user.id, ws_id)
    if ws_path is None:
        return jsonify({"error": "workspace not found"}), 404
    if any(child.name != ".stupidex.json" for child in ws_path.iterdir()):
        return jsonify({"error": "workspace is not empty"}), 409
    url = (data.get("url") or "").strip()
    branch = (data.get("branch") or "").strip() or None
    err = _validate_git_url(url)
    if err:
        return jsonify({"error": err}), 400
    if branch and (len(branch) > 200 or not re.fullmatch(r"[A-Za-z0-9._/-]+", branch)):
        return jsonify({"error": "invalid branch name"}), 400
    try:
        ws, stderr = workspaces_module.init_from_git(
            request.user.id,
            ws_id,
            url,
            branch,
            request.user.github_access_token,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except workspaces_module.RepositoryAccessError as exc:
        return jsonify({"error": str(exc)}), 403
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500
    except Exception as exc:
        return jsonify({"error": f"clone failed: {exc}"}), 500
    return jsonify({"ok": True, "workspace": ws.to_dict(), "stderr": stderr})


@app.route("/api/workspaces/<ws_id>/pull", methods=["POST"])
@login_required
@rate_limited("upload")
def workspaces_pull(ws_id):
    ok, output = workspaces_module.git_pull(
        request.user.id, ws_id, request.user.github_access_token
    )
    return jsonify({"ok": ok, "output": output}), 200 if ok else 400


@app.route("/api/workspaces/<ws_id>/shell", methods=["POST"])
@login_required
@rate_limited("default")
def workspaces_shell(ws_id):
    data = request.get_json(force=True) or {}
    cmd = (data.get("command") or "").strip()
    if not cmd:
        return jsonify({"error": "empty command"}), 400
    ws_path = workspaces_module.workspace_path(request.user.id, ws_id)
    if ws_path is None:
        return jsonify({"error": "workspace not found"}), 404
    from stupidex.llm.tools import run_shell as run_shell_tool

    output = run_shell_tool(
        cmd,
        cwd=str(ws_path),
        workspace_root=str(ws_path),
        github_token=request.user.github_access_token,
    )
    tree_changed = False
    if "stdout:" in output or "stderr:" in output:
        tree_changed = True
    return jsonify(
        {
            "output": output,
            "code": 0,
            "tree_changed": tree_changed,
        }
    )


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
    if not _path_within(target, ws_path):
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
    return jsonify(
        {
            "path": rel,
            "content": content,
            "size": target.stat().st_size,
        }
    )


# ============================================================
# Entry point
# ============================================================


def main():
    import os as _os

    # Log warning if GitHub OAuth is not configured
    if not _github_configured():
        app.logger.warning(
            "GitHub OAuth is not configured. Set GITHUB_CLIENT_ID, "
            "GITHUB_CLIENT_SECRET, and GITHUB_REDIRECT_URI environment variables "
            "to enable private repository cloning. See README.md for details."
        )

    host = _os.environ.get("STUPIDEX_HOST", "0.0.0.0")
    port = int(_os.environ.get("STUPIDEX_PORT", _os.environ.get("PORT", "5000")))
    debug = _os.environ.get("STUPIDEX_DEBUG") == "1"
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    main()
