"""Auth routes — Google OAuth, GitHub integration, email/password auth."""

from __future__ import annotations

import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request

from flask import jsonify, redirect, request

from .. import db
from ..config import has_api_key, load_config
from ..llm.providers import DEFAULT_FALLBACK_ID, PROVIDERS, list_providers
from ..services.auth_service import request_token, set_auth_cookie
from ..services.rate_limit import rate_limit_check
from ..web import app, login_required, rate_limited  # noqa: F811

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "")

_GITHUB_AUTH_URL = "https://github.com/login/oauth/authorize"
_GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
_GITHUB_USER_URL = "https://api.github.com/user"

_OAUTH_STATE_COOKIE = "stupidex_oauth_state"
_GITHUB_STATE_COOKIE = "stupidex_github_oauth_state"

# ---------------------------------------------------------------------------
# Redirect URI helpers
# ---------------------------------------------------------------------------


def _build_redirect_uri(base_env_var: str, path_suffix: str) -> str:
    frontend = os.environ.get(base_env_var, "").strip()
    if frontend:
        return frontend.rstrip("/") + path_suffix
    from flask import request as _req
    host = _req.host
    if "render.com" in host:
        return f"https://{host}{path_suffix}"
    port = os.environ.get("PORT", "5000")
    return f"http://localhost:{port}{path_suffix}"


def _github_configured() -> bool:
    return bool(GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET)


def _github_identity(access_token: str) -> tuple[str, str]:
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
    login = str(github_user.get("login") or "").strip()
    avatar_url = str(github_user.get("avatar_url") or "").strip()
    if not login:
        raise RuntimeError("GitHub did not return an account login")
    return login, avatar_url


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


# ---------------------------------------------------------------------------
# Google OAuth
# ---------------------------------------------------------------------------


@app.route("/api/auth/google", methods=["GET"])
def auth_google():
    if not GOOGLE_CLIENT_ID:
        return jsonify({"error": "Google OAuth not configured"}), 501
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(16)
    redirect_uri = _build_redirect_uri("FRONTEND_URL", "/api/auth/google/callback")
    params = urllib.parse.urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": f"{state}.{nonce}",
        "access_type": "offline",
        "prompt": "select_account",
    })
    resp = redirect(f"{_GOOGLE_AUTH_URL}?{params}")
    resp.set_cookie(_OAUTH_STATE_COOKIE, f"{state}.{nonce}", max_age=600, httponly=True, samesite="Lax", path="/")
    return resp


@app.route("/api/auth/google/callback", methods=["GET"])
def auth_google_callback():
    code = request.args.get("code", "")
    error = request.args.get("error", "")
    if error:
        return jsonify({"error": f"Google OAuth denied: {error}"}), 400
    if not code:
        return jsonify({"error": "missing authorization code"}), 400
    state_qs = request.args.get("state", "")
    state_cookie = request.cookies.get(_OAUTH_STATE_COOKIE, "")
    if not state_qs or not state_cookie or not secrets.compare_digest(state_qs, state_cookie):
        return jsonify({"error": "invalid OAuth state (possible CSRF)"}), 400
    redirect_uri = _build_redirect_uri("FRONTEND_URL", "/api/auth/google/callback")
    try:
        token_req = urllib.request.Request(
            _GOOGLE_TOKEN_URL,
            data=urllib.parse.urlencode({
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
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
    try:
        userinfo_req = urllib.request.Request(_GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"})
        with urllib.request.urlopen(userinfo_req, timeout=10) as resp:
            userinfo = json.loads(resp.read())
        email = (userinfo.get("email") or "").strip().lower()
        name = userinfo.get("name") or email.split("@")[0]
        picture = userinfo.get("picture") or ""
        if not email:
            return jsonify({"error": "Google did not return an email address"}), 400
    except Exception as e:
        return jsonify({"error": f"userinfo request failed: {e}"}), 500
    try:
        user, token = db.find_or_create_oauth_user(email, name, picture, "google")
    except Exception as e:
        return jsonify({"error": f"user creation failed: {e}"}), 500
    frontend = os.environ.get("FRONTEND_URL", "http://localhost:5000").strip() or "http://localhost:5000"
    parsed = urllib.parse.urlparse(frontend)
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    query["google"] = "connected"
    redirect_url = urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))
    resp = redirect(redirect_url)
    set_auth_cookie(resp, token)
    resp.set_cookie(_OAUTH_STATE_COOKIE, "", max_age=0, path="/")
    return resp


# ---------------------------------------------------------------------------
# GitHub integration
# ---------------------------------------------------------------------------


@app.route("/api/integrations/github", methods=["GET"])
@login_required
@rate_limited("default")
def github_integration_status():
    return jsonify({
        "configured": _github_configured(),
        "oauth_configured": _github_configured(),
        "token_connection_available": True,
        "connected": bool(request.user.github_access_token),
        "login": request.user.github_login,
        "avatar_url": request.user.github_avatar_url,
        "connected_at": request.user.github_connected_at,
        "scope": "repo",
    })


@app.route("/api/integrations/github/connect", methods=["GET"])
@login_required
@rate_limited("auth")
def github_integration_connect():
    if not _github_configured():
        return jsonify({"error": "GitHub OAuth is not configured", "detail": "Set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET"}), 501
    state = secrets.token_urlsafe(32)
    redirect_uri = _build_redirect_uri("FRONTEND_URL", "/api/integrations/github/callback")
    params = urllib.parse.urlencode({
        "client_id": GITHUB_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": "repo",
        "state": state,
        "allow_signup": "false",
    })
    resp = redirect(f"{_GITHUB_AUTH_URL}?{params}")
    resp.set_cookie(_GITHUB_STATE_COOKIE, state, max_age=600, httponly=True, samesite="Lax", path="/")
    return resp


@app.route("/api/integrations/github/callback", methods=["GET"])
@login_required
@rate_limited("auth")
def github_integration_callback():
    state_qs = request.args.get("state", "")
    state_cookie = request.cookies.get(_GITHUB_STATE_COOKIE, "")
    if not state_qs or not state_cookie or not secrets.compare_digest(state_qs, state_cookie):
        return jsonify({"error": "invalid OAuth state"}), 400
    if request.args.get("error"):
        resp = redirect(_frontend_redirect_url("denied"))
        resp.set_cookie(_GITHUB_STATE_COOKIE, "", max_age=0, path="/")
        return resp
    code = request.args.get("code", "")
    if not code:
        return jsonify({"error": "missing authorization code"}), 400
    if not _github_configured():
        return jsonify({"error": "GitHub OAuth is not configured"}), 501
    redirect_uri = _build_redirect_uri("FRONTEND_URL", "/api/integrations/github/callback")
    try:
        token_req = urllib.request.Request(
            _GITHUB_TOKEN_URL,
            data=urllib.parse.urlencode({
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
                "redirect_uri": redirect_uri,
            }).encode(),
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded", "User-Agent": "Stupidex/0.1"},
        )
        with urllib.request.urlopen(token_req, timeout=10) as token_resp:
            token_data = json.loads(token_resp.read())
        access_token = (token_data.get("access_token") or "").strip()
        granted_scopes = {item.strip() for item in (token_data.get("scope") or "").split(",")}
        if not access_token:
            raise RuntimeError("GitHub did not return an access token")
        if "repo" not in granted_scopes:
            raise RuntimeError("GitHub did not grant private repository access")
        login, avatar_url = _github_identity(access_token)
        db.update_github_connection(request.user.id, access_token, login, avatar_url)
    except Exception:
        resp = redirect(_frontend_redirect_url("error"))
        resp.set_cookie(_GITHUB_STATE_COOKIE, "", max_age=0, path="/")
        return resp
    resp = redirect(_frontend_redirect_url("connected"))
    resp.set_cookie(_GITHUB_STATE_COOKIE, "", max_age=0, path="/")
    return resp


@app.route("/api/integrations/github/token", methods=["POST"])
@login_required
@rate_limited("auth")
def github_integration_token():
    data = request.get_json(force=True) or {}
    access_token = str(data.get("token") or "").strip()
    if not 20 <= len(access_token) <= 500 or any(char.isspace() for char in access_token):
        return jsonify({"error": "invalid GitHub token"}), 400
    try:
        login, avatar_url = _github_identity(access_token)
    except urllib.error.HTTPError as exc:
        return jsonify({"error": "GitHub rejected this token"}), 401 if exc.code in (401, 403) else 502
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return jsonify({"error": "GitHub is temporarily unavailable"}), 502
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400
    db.update_github_connection(request.user.id, access_token, login, avatar_url)
    return jsonify({"ok": True, "login": login, "avatar_url": avatar_url})


@app.route("/api/integrations/github", methods=["DELETE"])
@login_required
@rate_limited("default")
def github_integration_disconnect():
    db.clear_github_connection(request.user.id)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Email/password auth
# ---------------------------------------------------------------------------


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
    return set_auth_cookie(jsonify({"user": user.to_dict(), "token": token}), token)


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
    return set_auth_cookie(jsonify({"user": user.to_dict(), "token": token}), token)


@app.route("/api/auth/enter", methods=["POST"])
@rate_limited("auth")
def auth_enter():
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
    return set_auth_cookie(jsonify({"user": user.to_dict(), "token": token}), token)


@app.route("/api/auth/logout", methods=["POST"])
@login_required
@rate_limited("auth")
def auth_logout():
    from ..services.auth_service import request_token as _rt, _AUTH_COOKIE
    token = _rt()
    db.logout_token(token)
    resp = jsonify({"ok": True})
    resp.delete_cookie(_AUTH_COOKIE, path="/")
    return resp


@app.route("/api/auth/me", methods=["GET"])
@login_required
@rate_limited("default")
def auth_me():
    return jsonify({"user": request.user.to_dict()})
