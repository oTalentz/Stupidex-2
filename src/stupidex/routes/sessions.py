"""Session routes — CRUD, chat, streaming, export, browser turns, agent tools."""

from __future__ import annotations

import json
import queue
import re
import secrets
import threading
import time

from collections.abc import Generator
from flask import Response, jsonify, request

from .. import db
from .. import workspaces as workspaces_module
from ..config import has_api_key, load_config
from ..llm.handle_input import (
    AGENT_BRIDGE_TOOLS,
    ALL_MODES,
    MODE_AGENT,
    build_context,
    execute_workspace_tool,
    stream_response,
)
from ..llm.providers import DEFAULT_FALLBACK_ID, PROVIDERS
from ..services.stream_manager import (
    claim_stream,
    get_stream,
    pop_stream,
    session_lock,
)
from ..services.validation import (
    MAX_CHAT_IMAGE_BYTES,
    MAX_CHAT_IMAGES,
    _CHAT_IMAGE_MIMES,
    validate_browser_tool_trace,
    validate_chat_images,
)
from ..web import app, login_required, rate_limited


# ===================================================================
# Session CRUD
# ===================================================================


@app.route("/api/sessions", methods=["GET"])
@login_required
@rate_limited("default")
def sessions_list():
    include_archived = request.args.get("include_archived") == "1"
    include_trashed = request.args.get("include_trashed") == "1"
    only_trashed = request.args.get("trashed") == "1"
    return jsonify([
        s.to_dict()
        for s in db.list_sessions(
            request.user.id,
            include_archived=include_archived,
            include_trashed=include_trashed or only_trashed,
            only_trashed=only_trashed,
        )
    ])


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
    if not model or model.lower() == "model":
        model = PROVIDERS[provider].default_model
    existing = db.list_sessions(request.user.id, include_archived=True, include_trashed=True)
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
    ev = get_stream(sid)
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
    if get_stream(sid):
        return jsonify({"error": "session is currently generating"}), 409
    db.clear_messages(sid)
    return jsonify({"ok": True})


# ===================================================================
# Export
# ===================================================================


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
    safe_title = re.sub(r"[^\w\-\. ]+", "_", s.title or "session")[:80] or "session"
    if fmt == "json":
        body = json.dumps({"session": s.to_dict(), "messages": [m.to_dict() for m in msgs]}, indent=2, ensure_ascii=False)
        return Response(body, mimetype="application/json", headers={"Content-Disposition": f'attachment; filename="{safe_title}.json"'})
    out = [f"# {s.title}", "", f"**Provider:** {s.provider}  ", f"**Model:** {s.model}  ", f"**Created:** {time.ctime(s.created_at)}", ""]
    for m in msgs:
        if m.role == "system" and m.metadata.get("error"):
            continue
        if m.role == "tool":
            continue
        if m.role == "user":
            out.extend(["## Você", "", m.content, ""])
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
                            out.append(json.dumps(json.loads(tc["arguments"]), indent=2))
                        except Exception:
                            out.append(tc["arguments"])
                        out.append("```")
                        out.append("")
            if m.content:
                out.extend([m.content, ""])
    body = "\n".join(out)
    return Response(body, mimetype="text/markdown; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{safe_title}.md"'})


# ===================================================================
# Browser turn (Puter model)
# ===================================================================


@app.route("/api/sessions/<sid>/browser-turn", methods=["POST"])
@login_required
@rate_limited("chat")
def session_browser_turn(sid):
    session = db.get_session_for_user(sid, request.user.id)
    if not session:
        return jsonify({"error": "session not found"}), 404
    if session.trashed:
        return jsonify({"error": "session is in trash"}), 409
    data = request.get_json(force=True) or {}
    provider_id = str(data.get("provider") or "").strip()
    provider = PROVIDERS.get(provider_id)
    if not provider or provider.runtime != "puter":
        return jsonify({"error": "provider is not a browser Puter model"}), 400
    user_text = str(data.get("message") or "").strip()
    assistant_text = str(data.get("response") or "").strip()
    regenerate = data.get("regenerate") is True
    if len(user_text) > 100_000 or len(assistant_text) > 500_000:
        return jsonify({"error": "message too long"}), 400
    if not assistant_text:
        return jsonify({"error": "empty assistant response"}), 400
    if regenerate and not db.get_last_user_message(sid):
        return jsonify({"error": "no user message to regenerate from"}), 400
    images = data.get("images") or []
    if not isinstance(images, list) or len(images) > MAX_CHAT_IMAGES:
        return jsonify({"error": "invalid image metadata"}), 400
    image_meta = []
    for image in images:
        if not isinstance(image, dict):
            return jsonify({"error": "invalid image metadata"}), 400
        name = str(image.get("name") or "image")[:255]
        mime = str(image.get("mime") or "")[:100]
        size = image.get("size")
        if mime not in _CHAT_IMAGE_MIMES or not isinstance(size, int) or size < 0 or size > MAX_CHAT_IMAGE_BYTES:
            return jsonify({"error": "invalid image metadata"}), 400
        image_meta.append({"name": name, "mime": mime, "size": size})
    if not regenerate and not user_text and not image_meta:
        return jsonify({"error": "empty user message"}), 400
    tool_trace, trace_error = validate_browser_tool_trace(data.get("tool_trace"), AGENT_BRIDGE_TOOLS)
    if trace_error:
        return jsonify({"error": trace_error}), 400
    if not regenerate:
        metadata = {}
        if image_meta:
            metadata["images"] = image_meta
        if data.get("web_search") is True:
            metadata["web_search"] = True
        db.append_message(sid, "user", user_text, metadata=metadata or None)
        db.auto_title(sid, user_text or "Análise de imagem")
    for item in tool_trace:
        serialized = json.dumps(item["arguments"], ensure_ascii=False)
        db.append_message(sid, "assistant", "", type_="tool_call", tool_calls=[{"id": item["id"], "name": item["name"], "arguments": serialized}], metadata={"runtime": "puter-agent"})
        db.append_message(sid, "tool", item["result"], type_="tool_result", tool_call_id=item["id"], metadata={"runtime": "puter-agent", "tool_name": item["name"], "error": item["error"]})
    model = str(data.get("model") or provider.default_model).strip()[:200]
    db.append_message(sid, "assistant", assistant_text, metadata={"runtime": "puter", "model": model})
    return jsonify({"ok": True, "title": db.get_session(sid).title})


# ===================================================================
# Agent tool bridge (Puter model)
# ===================================================================


@app.route("/api/sessions/<sid>/agent-tool", methods=["POST"])
@login_required
@rate_limited("chat")
def session_agent_tool(sid):
    session = db.get_session_for_user(sid, request.user.id)
    if not session:
        return jsonify({"error": "session not found"}), 404
    if session.trashed:
        return jsonify({"error": "session is in trash"}), 409
    data = request.get_json(force=True) or {}
    provider = PROVIDERS.get(str(data.get("provider") or ""))
    if not provider or provider.runtime != "puter" or not provider.supports_agent_bridge:
        return jsonify({"error": "provider does not support the agent bridge"}), 400
    if data.get("agent_enabled") is not True:
        return jsonify({"error": "agent mode is not authorized"}), 403
    name = str(data.get("name") or "").strip()
    arguments = data.get("arguments") or {}
    if not isinstance(arguments, dict):
        return jsonify({"error": "tool arguments must be an object"}), 400
    if len(json.dumps(arguments, ensure_ascii=False)) > 500_000:
        return jsonify({"error": "tool arguments are too large"}), 400
    ctx = build_context(
        provider_id=DEFAULT_FALLBACK_ID,
        api_key_override=None,
        user_id=request.user.id,
        github_token=request.user.github_access_token,
        mode=MODE_AGENT,
    )
    result = execute_workspace_tool(name, arguments, ctx)
    is_error = result.startswith(("ERROR:", "SECURITY:"))
    active = workspaces_module.get_active_workspace(request.user.id)
    tree_changed = not is_error and name in {"write_file", "edit_file", "mkdir", "delete"}
    if not is_error and name in {"run_shell", "git"}:
        tree_changed = True
    if tree_changed and active:
        workspaces_module.touch(request.user.id, active.id)
    return jsonify({"id": f"puter_{secrets.token_hex(12)}", "name": name, "result": result, "error": is_error, "tree_changed": tree_changed})


# ===================================================================
# Streaming chat
# ===================================================================


@app.route("/api/sessions/<sid>/stop", methods=["POST"])
@login_required
@rate_limited("chat")
def session_stop(sid):
    if not db.get_session_for_user(sid, request.user.id):
        return jsonify({"error": "session not found"}), 404
    ev = get_stream(sid)
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
    provider = PROVIDERS.get(provider_id, PROVIDERS[DEFAULT_FALLBACK_ID])
    if provider.runtime != "server":
        return jsonify({"error": "the selected Puter model must run in the browser"}), 400
    user_api_key = request.user.api_key or data.get("api_key")
    if provider.needs_api_key and not user_api_key and not has_api_key():
        return jsonify({"error": "no LLM API key configured"}), 503
    agent_mode = data.get("mode") or MODE_AGENT
    if agent_mode not in ALL_MODES:
        agent_mode = MODE_AGENT
    ctx = build_context(
        provider_id=provider_id,
        api_key_override=user_api_key,
        user_id=request.user.id,
        model_override=(data.get("model") or s.model),
        github_token=request.user.github_access_token,
        mode=agent_mode,
    )
    ctx.web_search_enabled = bool(last_user.metadata.get("web_search"))
    ctx.session_id = sid
    ctx.cancel_event = claim_stream(sid)
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
        error_id = secrets.token_hex(6)
        from ..web import app as _app
        _app.logger.error("session_chat fatal id=%s: %s\n%s", error_id, exc, tb)
        return jsonify({"error": "internal server error", "error_id": error_id}), 500


# ===================================================================
# Chat implementation
# ===================================================================


def _session_chat_impl(sid: str) -> Response:
    session = db.get_session_for_user(sid, request.user.id)
    if not session:
        return jsonify({"error": "session not found"}), 404
    if session.trashed:
        return jsonify({"error": "session is in trash"}), 409
    lock = session_lock(sid)
    if not lock.acquire(blocking=False):
        return jsonify({"error": "session is busy — try again shortly"}), 429
    try:
        session = db.get_session_for_user(sid, request.user.id)
        if not session:
            return jsonify({"error": "session not found"}), 404
        if session.trashed:
            return jsonify({"error": "session is in trash"}), 409
        data = request.get_json(force=True) or {}
        user_msg = (data.get("message") or "").strip()
        images, image_error = validate_chat_images(data.get("images"))
        if image_error:
            return jsonify({"error": image_error}), 400
        if not user_msg and not images:
            return jsonify({"error": "empty message"}), 400
        if len(user_msg) > 100_000:
            return jsonify({"error": "message too long (max 100k chars)"}), 400
        provider_id = data.get("provider") or session.provider
        provider = PROVIDERS.get(provider_id, PROVIDERS[DEFAULT_FALLBACK_ID])
        if provider.runtime != "server":
            return jsonify({"error": "the selected Puter model must run in the browser"}), 400
        if images and not provider.supports_vision:
            return jsonify({"error": "the selected model does not support image input"}), 400
        user_api_key = request.user.api_key or data.get("api_key")
        if provider.needs_api_key and not user_api_key and not has_api_key():
            return jsonify({"error": "no LLM API key configured. Set DEEPSEEK_API_KEY in the server env, or add your own key in Settings."}), 503
        agent_mode = data.get("mode") or MODE_AGENT
        if agent_mode not in ALL_MODES:
            agent_mode = MODE_AGENT
        ctx = build_context(
            provider_id=provider_id,
            api_key_override=user_api_key,
            user_id=request.user.id,
            model_override=(data.get("model") or session.model),
            github_token=request.user.github_access_token,
            mode=agent_mode,
        )
        ctx.web_search_enabled = data.get("web_search") is True
        ctx.session_id = sid
        ctx.cancel_event = claim_stream(sid)
        if ctx.cancel_event is None:
            # Give the previous stream 1s to finish, then try once more
            import time as _time
            existing = get_stream(sid)
            if existing:
                existing.set()  # signal cancellation
            _time.sleep(1.0)
            ctx.cancel_event = claim_stream(sid)
            if ctx.cancel_event is None:
                return jsonify({"error": "session is already generating"}), 409
        return _stream_response(sid, user_msg, ctx, images=images)
    finally:
        lock.release()


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
            for event in stream_response(sid, user_text, ctx, regenerate_user_msg_id=regenerate_user_msg_id, images=images):
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
            from ..web import app as _app
            _app.logger.error("stream fatal id=%s sid=%s: %s\n%s", error_id, sid, exc, _tb.format_exc(limit=5))
        finally:
            pop_stream(sid, ctx.cancel_event)
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

    return Response(event_stream(), mimetype="text/event-stream", headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})
