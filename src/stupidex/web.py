"""Stupidex web server — Flask + SSE streaming, with auth, CORS, rate limit.

Endpoints:
  GET  /                                  → SPA
  GET  /api/health                        → liveness probe
  GET  /api/providers                     → provider list
  GET  /api/config                        → current config (no secrets leaked)
  POST /api/config                        → update provider/model/api_key

  GET  /api/sessions                      → list sessions (newest first)
  POST /api/sessions                      → create session
  PATCH /api/sessions/<id>                → rename / pin / archive
  DELETE /api/sessions/<id>               → delete
  GET  /api/sessions/search?q=...         → search by title/content
  GET  /api/sessions/<id>/messages        → message history
  POST /api/sessions/<id>/clear           → clear messages (keep session)
  POST /api/sessions/<id>/regenerate      → redo last assistant turn
  POST /api/sessions/<id>/stop            → cancel current stream
  POST /api/sessions/<id>/chat            → SSE stream
  GET  /api/sessions/<id>/export         → download JSON or Markdown

  GET  /api/workspaces                    → list workspaces
  POST /api/workspaces                    → create empty workspace
  DELETE /api/workspaces/<id>             → delete workspace
  POST /api/workspaces/<id>/activate      → set active workspace
  POST /api/workspaces/<id>/upload        → upload files (multipart)
  POST /api/workspaces/<id>/upload-zip    → upload + extract zip
  POST /api/workspaces/<id>/clone         → git clone
  POST /api/workspaces/<id>/pull          → git pull
  GET  /api/workspaces/<id>/tree         → file tree
  GET  /api/workspaces/<id>/file?path=   → file content
"""
import base64
import hmac
import io
import json
import os
import queue
import threading
import time
import zipfile
from collections.abc import Generator
from functools import wraps
from hashlib import sha256
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request, send_from_directory

from . import db, workspaces as workspaces_module
from .config import load_config, update_config
from .llm.handle_input import build_context, stream_response
from .llm.providers import DEFAULT_FALLBACK_ID, PROVIDERS, list_providers

app = Flask(__name__, static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200 MB upload limit

# ============================================================
# SECURITY: simple bearer-token auth
# ============================================================

AUTH_TOKEN = os.environ.get("STUPIDEX_TOKEN")  # if set, all API routes require it
CORS_ORIGINS = os.environ.get("STUPIDEX_CORS", "*").split(",")


def _check_auth() -> bool:
    if not AUTH_TOKEN:
        return True
    provided = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not provided:
        return False
    return hmac.compare_digest(provided.encode(), AUTH_TOKEN.encode())


def require_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not _check_auth():
            return jsonify({"error": "unauthorized"}), 401
        return fn(*args, **kwargs)
    return wrapper


@app.after_request
def add_cors(resp):
    origin = request.headers.get("Origin", "*")
    if "*" in CORS_ORIGINS or origin in CORS_ORIGINS:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
    return resp


@app.route("/api/<path:_>", methods=["OPTIONS"])
def preflight(_):
    return Response("", 204)


# ============================================================
# Static / health
# ============================================================

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "ts": time.time()})


# ============================================================
# Providers / Config
# ============================================================

@app.route("/api/providers", methods=["GET"])
@require_auth
def providers():
    return jsonify(list_providers())


@app.route("/api/config", methods=["GET"])
@require_auth
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
@require_auth
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
@require_auth
def sessions_list():
    include_archived = request.args.get("include_archived") == "1"
    return jsonify([s.to_dict() for s in db.list_sessions(include_archived=include_archived)])


@app.route("/api/sessions/search", methods=["GET"])
@require_auth
def sessions_search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify([])
    return jsonify([s.to_dict() for s in db.search_sessions(q)])


@app.route("/api/sessions", methods=["POST"])
@require_auth
def sessions_create():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    provider = data.get("provider") or cfg.provider
    if provider not in PROVIDERS:
        provider = DEFAULT_FALLBACK_ID
    model = data.get("model") or cfg.model
    s = db.create_session(provider, model)
    return jsonify(s.to_dict())


@app.route("/api/sessions/<sid>", methods=["PATCH"])
@require_auth
def sessions_update(sid):
    data = request.get_json(force=True) or {}
    if "title" in data:
        if not db.rename_session(sid, data["title"]):
            return jsonify({"error": "not found"}), 404
    if "pinned" in data:
        if not db.set_pinned(sid, bool(data["pinned"])):
            return jsonify({"error": "not found"}), 404
    if "archived" in data:
        if not db.set_archived(sid, bool(data["archived"])):
            return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/sessions/<sid>", methods=["DELETE"])
@require_auth
def sessions_delete(sid):
    if not db.delete_session(sid):
        return jsonify({"error": "not found"}), 404
    # Also cancel any in-flight stream for this session
    _STREAMS.pop(sid, None)
    return jsonify({"ok": True})


@app.route("/api/sessions/<sid>/messages", methods=["GET"])
@require_auth
def session_messages(sid):
    return jsonify([m.to_dict() for m in db.get_messages(sid)])


@app.route("/api/sessions/<sid>/clear", methods=["POST"])
@require_auth
def session_clear(sid):
    db.clear_messages(sid)
    return jsonify({"ok": True})


@app.route("/api/sessions/<sid>/export", methods=["GET"])
@require_auth
def session_export(sid):
    fmt = request.args.get("format", "md").lower()
    s = db.get_session(sid)
    if not s:
        return jsonify({"error": "not found"}), 404
    msgs = db.get_messages(sid)
    if fmt == "json":
        body = json.dumps({"session": s.to_dict(), "messages": [m.to_dict() for m in msgs]},
                          indent=2, ensure_ascii=False)
        return Response(body, mimetype="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{s.title}.json"'})
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
                    headers={"Content-Disposition": f'attachment; filename="{s.title}.md"'})


# ============================================================
# Streaming chat (with cancel support)
# ============================================================

_STREAMS: dict[str, threading.Event] = {}


@app.route("/api/sessions/<sid>/stop", methods=["POST"])
@require_auth
def session_stop(sid):
    ev = _STREAMS.get(sid)
    if ev:
        ev.set()
    return jsonify({"ok": True})


@app.route("/api/sessions/<sid>/regenerate", methods=["POST"])
@require_auth
def session_regenerate(sid):
    data = request.get_json(silent=True) or {}
    user_text = (data.get("message") or "").strip()
    last_user = db.get_last_user_message(sid)
    if not last_user:
        return jsonify({"error": "no user message to regenerate from"}), 400

    session = db.get_session(sid)
    if not session:
        return jsonify({"error": "session not found"}), 404

    # If the last user message has no text but a new one was provided, edit it in place
    if not user_text:
        user_text = last_user.content

    ctx = build_context(
        provider_id=session.provider,
        api_key_override=data.get("api_key"),
    )
    ctx.session_id = sid
    if data.get("model"):
        ctx.model = data["model"].strip()
    ctx.cancel_event = threading.Event()
    _STREAMS[sid] = ctx.cancel_event
    try:
        return _stream_response(sid, user_text, ctx, regenerate_user_msg_id=last_user.id)
    finally:
        if _STREAMS.get(sid) is ctx.cancel_event:
            _STREAMS.pop(sid, None)


@app.route("/api/sessions/<sid>/chat", methods=["POST"])
@require_auth
def session_chat(sid):
    try:
        return _session_chat_impl(sid)
    except Exception as exc:
        import traceback
        tb = traceback.format_exc(limit=10)
        app.logger.error(f"session_chat fatal: {exc}\n{tb}")
        return jsonify({"error": f"{type(exc).__name__}: {exc}", "trace": tb}), 500


def _session_chat_impl(sid: str) -> Response:
    session = db.get_session(sid)
    if not session:
        return jsonify({"error": "session not found"}), 404

    data = request.get_json(force=True) or {}
    user_msg = (data.get("message") or "").strip()
    if not user_msg:
        return jsonify({"error": "empty message"}), 400

    ctx = build_context(
        provider_id=data.get("provider") or session.provider,
        api_key_override=data.get("api_key"),
    )
    ctx.session_id = sid
    if data.get("model"):
        ctx.model = data["model"].strip()
    ctx.cancel_event = threading.Event()
    _STREAMS[sid] = ctx.cancel_event
    try:
        return _stream_response(sid, user_msg, ctx)
    finally:
        if _STREAMS.get(sid) is ctx.cancel_event:
            _STREAMS.pop(sid, None)


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
            # Client disconnected — set the cancel flag so the LLM stream stops.
            ctx.cancel_event.set()
            raise

    return Response(event_stream(), mimetype="text/event-stream",
                    headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


# ============================================================
# Workspaces
# ============================================================

@app.route("/api/workspaces", methods=["GET"])
@require_auth
def workspaces_list():
    ws_list = [w.to_dict() for w in workspaces_module.list_workspaces()]
    active = workspaces_module.get_active_workspace()
    return jsonify({
        "workspaces": ws_list,
        "active_id": active.id if active else None,
    })


@app.route("/api/workspaces", methods=["POST"])
@require_auth
def workspaces_create():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip() or "Workspace"
    ws = workspaces_module.create_empty(name)
    if not workspaces_module.get_active_workspace():
        workspaces_module.set_active_workspace(ws.id)
    return jsonify({"ok": True, "workspace": ws.to_dict()})


@app.route("/api/workspaces/<ws_id>", methods=["DELETE"])
@require_auth
def workspaces_delete(ws_id):
    if not workspaces_module.delete_workspace(ws_id):
        return jsonify({"error": "workspace not found"}), 404
    active = workspaces_module.get_active_workspace()
    return jsonify({"ok": True, "active_id": active.id if active else None})


@app.route("/api/workspaces/<ws_id>/activate", methods=["POST"])
@require_auth
def workspaces_activate(ws_id):
    if not workspaces_module.set_active_workspace(ws_id):
        return jsonify({"error": "workspace not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/workspaces/<ws_id>/upload", methods=["POST"])
@require_auth
def workspaces_upload(ws_id):
    ws_path = workspaces_module.workspace_path(ws_id)
    if not ws_path:
        return jsonify({"error": "workspace not found"}), 404

    rel_path = request.form.get("path", "").strip().lstrip("/\\")
    target = ws_path / rel_path if rel_path else ws_path
    target.mkdir(parents=True, exist_ok=True)

    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "no files in upload"}), 400

    saved = []
    for f in files:
        rel = (f.filename or "").replace("\\", "/").lstrip("/")
        if not rel or rel.endswith("/"):
            continue
        # Block path traversal
        dest = (target / rel).resolve()
        if not str(dest).startswith(str(ws_path.resolve())):
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        f.save(str(dest))
        saved.append(rel)

    workspaces_module.touch(ws_id)
    workspaces_module.init_from_upload(ws_id)
    ws = workspaces_module.get_workspace(ws_id)
    return jsonify({"ok": True, "saved": saved, "workspace": ws.to_dict()})


@app.route("/api/workspaces/<ws_id>/upload-zip", methods=["POST"])
@require_auth
def workspaces_upload_zip(ws_id):
    ws_path = workspaces_module.workspace_path(ws_id)
    if not ws_path:
        return jsonify({"error": "workspace not found"}), 404
    if any(ws_path.iterdir()):
        return jsonify({"error": "workspace is not empty — delete it first or use a fresh one"}), 400

    f = request.files.get("file")
    if not f:
        return jsonify({"error": "no file uploaded"}), 400
    data = f.read()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for member in zf.namelist():
                if member.endswith("/"):
                    continue
                if ".." in Path(member).parts:
                    continue
                target = ws_path / member
                if not str(target.resolve()).startswith(str(ws_path.resolve())):
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(target, "wb") as dst:
                    dst.write(src.read())
    except zipfile.BadZipFile:
        return jsonify({"error": "not a valid .zip file"}), 400

    workspaces_module.touch(ws_id)
    workspaces_module.init_from_upload(ws_id)
    ws = workspaces_module.get_workspace(ws_id)
    return jsonify({"ok": True, "workspace": ws.to_dict()})


@app.route("/api/workspaces/<ws_id>/clone", methods=["POST"])
@require_auth
def workspaces_clone(ws_id):
    data = request.get_json(force=True) or {}
    url = (data.get("url") or "").strip()
    branch = (data.get("branch") or "").strip() or None
    if not url:
        return jsonify({"error": "url is required"}), 400
    try:
        ws, stderr = workspaces_module.init_from_git(ws_id, url, branch)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500
    except Exception as exc:
        return jsonify({"error": f"clone failed: {exc}"}), 500
    return jsonify({"ok": True, "workspace": ws.to_dict(), "stderr": stderr})


@app.route("/api/workspaces/<ws_id>/pull", methods=["POST"])
@require_auth
def workspaces_pull(ws_id):
    ok, output = workspaces_module.git_pull(ws_id)
    return jsonify({"ok": ok, "output": output})


@app.route("/api/workspaces/<ws_id>/tree", methods=["GET"])
@require_auth
def workspaces_tree(ws_id):
    return jsonify({"tree": workspaces_module.file_tree(ws_id)})


@app.route("/api/workspaces/<ws_id>/file", methods=["GET"])
@require_auth
def workspaces_file(ws_id):
    ws_path = workspaces_module.workspace_path(ws_id)
    if not ws_path:
        return jsonify({"error": "workspace not found"}), 404
    rel = (request.args.get("path") or "").strip().lstrip("/\\")
    if not rel:
        return jsonify({"error": "path required"}), 400
    target = (ws_path / rel).resolve()
    if not str(target).startswith(str(ws_path.resolve())):
        return jsonify({"error": "invalid path"}), 400
    if not target.is_file():
        return jsonify({"error": "not a file"}), 404
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
# CWD
# ============================================================

@app.route("/api/cwd", methods=["GET"])
@require_auth
def cwd():
    return jsonify({"cwd": os.getcwd()})


# ============================================================
# Entry point
# ============================================================

def main():
    import os
    host = os.environ.get("STUPIDEX_HOST", "0.0.0.0")
    port = int(os.environ.get("STUPIDEX_PORT", os.environ.get("PORT", "5000")))
    debug = os.environ.get("STUPIDEX_DEBUG") == "1"
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    main()
