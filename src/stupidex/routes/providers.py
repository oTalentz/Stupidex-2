"""Providers and config routes."""

from __future__ import annotations

import os

from flask import jsonify, request

from .. import db
from ..config import has_api_key, load_config
from ..llm.providers import DEFAULT_FALLBACK_ID, PROVIDERS, list_providers
from ..web import app, login_required, rate_limited


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
    if not model or model.strip() == "" or model.lower() == "model":
        model = PROVIDERS[provider].default_model
    return jsonify({
        "provider": provider,
        "model": model,
        "custom_model": custom_model,
        "has_api_key": bool(request.user.api_key or cfg.api_key),
        "shell_approval_mode": request.user.shell_approval_mode or os.environ.get("STUPIDEX_SHELL_APPROVAL_MODE", "auto"),
    })


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
    if not model or model.lower() == "model":
        model = PROVIDERS[provider].default_model
    if len(custom_model) > 200:
        return jsonify({"error": "model name too long"}), 400
    shell_mode = (data.get("shell_approval_mode") or "").strip()
    if shell_mode and shell_mode not in ("auto", "ask"):
        return jsonify({"error": "shell_approval_mode must be 'auto' or 'ask'"}), 400
    db.update_user_config(
        request.user.id,
        provider=provider,
        model=model,
        custom_model=custom_model,
        api_key=str(api_key).strip() if api_key else None,
        clear_api_key=bool(data.get("clear_api_key")),
        shell_approval_mode=shell_mode or None,
    )
    server_key = load_config().api_key
    has_key = bool(api_key or (request.user.api_key and not data.get("clear_api_key")) or server_key)
    return jsonify({"ok": True, "has_api_key": has_key, "provider": provider, "model": model, "custom_model": custom_model})
