"""Auth helpers — token, cookie, secret management."""

from __future__ import annotations

import os
from pathlib import Path

from flask import Response, request

_AUTH_COOKIE = "stupidex_auth"


def load_or_create_secret(secret_file: Path) -> bytes:
    if secret_file.exists():
        return secret_file.read_bytes()
    import secrets as _secrets
    key = _secrets.token_bytes(64)
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    secret_file.write_bytes(key)
    return key


def request_token() -> str:
    raw = request.headers.get("Authorization", "")
    bearer = raw.removeprefix("Bearer ").strip()
    return bearer or request.cookies.get(_AUTH_COOKIE, "").strip()


def set_auth_cookie(resp: Response, token: str) -> Response:
    is_production = (
        os.environ.get("FLASK_ENV") == "production"
        or os.environ.get("RAILWAY_ENV") == "production"
    )
    use_secure = is_production and (
        request.is_secure or request.headers.get("X-Forwarded-Proto") == "https"
    )
    resp.set_cookie(
        _AUTH_COOKIE, token,
        max_age=86400 * 30, httponly=True, secure=use_secure,
        samesite="Lax", path="/",
    )
    return resp
