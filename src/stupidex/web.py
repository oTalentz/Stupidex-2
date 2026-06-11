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

  GET  /api/cwd                           → current working directory
"""
import io
import json
import os
import queue
import threading
import time
import zipfile
from collections.abc import Generator
from functools import wraps
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

from . import db, workspaces as workspaces_module
from .config import DATA_DIR, load_config, update_config
from .llm.handle_input import build_context, stream_response
from .llm.providers import DEFAULT_FALLBACK_ID, PROVIDERS, list_providers

app = Flask(__name__, static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200 MB upload limit

CORS_ORIGINS = os.environ.get("STUPIDEX_CORS", "*").split(",")


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


# ============================================================
# CORS
# ============================================================

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
# Auth endpoints
# ============================================================

@app.route("/api/auth/register", methods=["POST"])
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
def auth_logout():
    raw = request.headers.get("Authorization", "")
    token = raw.removeprefix("Bearer ").strip()
    db.logout_token(token)
    return jsonify({"ok": True})


@app.route("/api/auth/me", methods=["GET"])
@login_required
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
def sessions_list():
    include_archived = request.args.get("include_archived") == "1"
    return jsonify([s.to_dict() for s in db.list_sessions(request.user.id, include_archived=include_archived)])


@app.route("/api/sessions/search", methods=["GET"])
@login_required
def sessions_search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify([])
    return jsonify([s.to_dict() for s in db.search_sessions(request.user.id, q)])


@app.route("/api/sessions", methods=["POST"])
@login_required
def sessions_create():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    provider = data.get("provider") or cfg.provider
    if provider not in PROVIDERS:
        provider = DEFAULT_FALLBACK_ID
    model = data.get("model") or cfg.model
    s = db.create_session(request.user.id, provider, model)
    return jsonify(s.to_dict())


@app.route("/api/sessions/<sid>", methods=["PATCH"])
@login_required
def sessions_update(sid):
    s = db.get_session_for_user(sid, request.user.id)
    if not s:
        return jsonify({"error": "not found"}), 404
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
@login_required
def sessions_delete(sid):
    s = db.get_session_for_user(sid, request.user.id)
    if not s:
        return jsonify({"error": "not found"}), 404
    if not db.delete_session(sid):
        return jsonify({"error": "not found"}), 404
    _STREAMS.pop(sid, None)
    return jsonify({"ok": True})


@app.route("/api/sessions/<sid>/messages", methods=["GET"])
@login_required
def session_messages(sid):
    s = db.get_session_for_user(sid, request.user.id)
    if not s:
        return jsonify({"error": "not found"}), 404
    return jsonify([m.to_dict() for m in db.get_messages(sid)])


@app.route("/api/sessions/<sid>/clear", methods=["POST"])
@login_required
def session_clear(sid):
    s = db.get_session_for_user(sid, request.user.id)
    if not s:
        return jsonify({"error": "not found"}), 404
    db.clear_messages(sid)
    return jsonify({"ok": True})


@app.route("/api/sessions/<sid>/export", methods=["GET"])
@login_required
def session_export(sid):
    fmt = request.args.get("format", "md").lower()
    s = db.get_session_for_user(sid, request.user.id)
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
@login_required
def session_stop(sid):
    ev = _STREAMS.get(sid)
    if ev:
        ev.set()
    return jsonify({"ok": True})


@app.route("/api/sessions/<sid>/regenerate", methods=["POST"])
@login_required
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
    _STREAMS[sid] = ctx.cancel_event
    try:
        return _stream_response(sid, user_text, ctx, regenerate_user_msg_id=last_user.id)
    finally:
        if _STREAMS.get(sid) is ctx.cancel_event:
            _STREAMS.pop(sid, None)


@app.route("/api/sessions/<sid>/chat", methods=["POST"])
@login_required
def session_chat(sid):
    try:
        return _session_chat_impl(sid)
    except Exception as exc:
        import traceback
        tb = traceback.format_exc(limit=10)
        app.logger.error(f"session_chat fatal: {exc}\n{tb}")
        return jsonify({"error": f"{type(exc).__name__}: {exc}", "trace": tb}), 500


def _session_chat_impl(sid: str) -> Response:
    session = db.get_session_for_user(sid, request.user.id)
    if not session:
        return jsonify({"error": "session not found"}), 404

    data = request.get_json(force=True) or {}
    user_msg = (data.get("message") or "").strip()
    if not user_msg:
        return jsonify({"error": "empty message"}), 400

    user_api_key = request.user.api_key or data.get("api_key")
    ctx = build_context(
        provider_id=data.get("provider") or session.provider,
        api_key_override=user_api_key,
        user_id=request.user.id,
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
            ctx.cancel_event.set()
            raise

    return Response(event_stream(), mimetype="text/event-stream",
                    headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


# ============================================================
# Workspaces (per-user scoped)
# ============================================================

def get_user_workspace_dir(user_id: str) -> Path:
    return DATA_DIR / "workspaces" / user_id


@app.route("/api/workspaces", methods=["GET"])
@login_required
def workspaces_list():
    ws_list = [w.to_dict() for w in workspaces_module.list_workspaces(request.user.id)]
    active = workspaces_module.get_active_workspace(request.user.id)
    return jsonify({
        "workspaces": ws_list,
        "active_id": active.id if active else None,
    })


@app.route("/api/workspaces", methods=["POST"])
@login_required
def workspaces_create():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip() or "Workspace"
    ws = workspaces_module.create_empty(request.user.id, name)
    if not workspaces_module.get_active_workspace(request.user.id):
        workspaces_module.set_active_workspace(request.user.id, ws.id)
    return jsonify({"ok": True, "workspace": ws.to_dict()})


@app.route("/api/workspaces/<ws_id>", methods=["DELETE"])
@login_required
def workspaces_delete(ws_id):
    if not workspaces_module.delete_workspace(request.user.id, ws_id):
        return jsonify({"error": "workspace not found"}), 404
    active = workspaces_module.get_active_workspace(request.user.id)
    return jsonify({"ok": True, "active_id": active.id if active else None})


@app.route("/api/workspaces/<ws_id>/activate", methods=["POST"])
@login_required
def workspaces_activate(ws_id):
    if not workspaces_module.set_active_workspace(request.user.id, ws_id):
        return jsonify({"error": "workspace not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/workspaces/<ws_id>/upload", methods=["POST"])
@login_required
def workspaces_upload(ws_id):
    ws_path = workspaces_module.workspace_path(request.user.id, ws_id)
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
        dest = (target / rel).resolve()
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

    workspaces_module.touch(request.user.id, ws_id)
    workspaces_module.init_from_upload(request.user.id, ws_id)
    ws = workspaces_module.get_workspace(request.user.id, ws_id)
    return jsonify({"ok": True, "workspace": ws.to_dict()})


@app.route("/api/workspaces/<ws_id>/clone", methods=["POST"])
@login_required
def workspaces_clone(ws_id):
    data = request.get_json(force=True) or {}
    url = (data.get("url") or "").strip()
    branch = (data.get("branch") or "").strip() or None
    if not url:
        return jsonify({"error": "url is required"}), 400
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
def workspaces_pull(ws_id):
    ok, output = workspaces_module.git_pull(request.user.id, ws_id)
    return jsonify({"ok": ok, "output": output})


@app.route("/api/workspaces/<ws_id>/tree", methods=["GET"])
@login_required
def workspaces_tree(ws_id):
    return jsonify({"tree": workspaces_module.file_tree(request.user.id, ws_id)})


@app.route("/api/workspaces/<ws_id>/file", methods=["GET"])
@login_required
def workspaces_file(ws_id):
    ws_path = workspaces_module.workspace_path(request.user.id, ws_id)
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
@login_required
def cwd():
    return jsonify({"cwd": os.getcwd()})


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
