"""Workspace routes — CRUD, upload, clone, shell, tree, file access."""

from __future__ import annotations

import json
import os as _os
import re
import shutil
import tempfile
import urllib.parse
import zipfile
from pathlib import Path

from flask import jsonify, request

from .. import workspaces as workspaces_module
from ..config import DATA_DIR
from ..services.validation import path_within, validate_git_url
from ..web import app, login_required, rate_limited


def get_user_workspace_dir(user_id: str) -> Path:
    return DATA_DIR / "workspaces" / user_id


# ===================================================================
# CRUD
# ===================================================================


@app.route("/api/workspaces", methods=["GET"])
@login_required
@rate_limited("default")
def workspaces_list():
    ws_list = [w.to_dict() for w in workspaces_module.list_workspaces(request.user.id)]
    active = workspaces_module.get_active_workspace(request.user.id)
    return jsonify({"workspaces": ws_list, "active_id": active.id if active else None})


@app.route("/api/workspaces", methods=["POST"])
@login_required
@rate_limited("default")
def workspaces_create():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip() or "Workspace"
    if len(name) > 100:
        return jsonify({"error": "name too long (max 100 chars)"}), 400
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


# ===================================================================
# Upload
# ===================================================================


@app.route("/api/workspaces/<ws_id>/upload", methods=["POST"])
@login_required
@rate_limited("upload")
def workspaces_upload(ws_id):
    ws_path = workspaces_module.workspace_path(request.user.id, ws_id)
    if not ws_path:
        return jsonify({"error": "workspace not found"}), 404
    rel_path = request.form.get("path", "").strip().lstrip("/\\")
    target = (ws_path / rel_path).resolve() if rel_path else ws_path.resolve()
    if not path_within(target, ws_path):
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
        if ".." in Path(rel).parts or "\x00" in rel:
            continue
        dest = (target / rel).resolve()
        if not path_within(dest, ws_path):
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        existed = dest.is_file()
        old_size = dest.stat().st_size if existed else 0
        projected_files = total_files if existed else total_files + 1
        if projected_files > workspaces_module.MAX_WORKSPACE_FILES:
            return jsonify({"error": "workspace file limit exceeded"}), 413
        written = 0
        temp_upload = None
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
                    if total_bytes - old_size + written > workspaces_module.MAX_WORKSPACE_BYTES:
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
        return jsonify({"error": "workspace is not empty — delete it first or use a fresh one"}), 400
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "no file uploaded"}), 400
    tmp_path = None
    stage = None
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


# ===================================================================
# Git operations
# ===================================================================


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
    err = validate_git_url(url)
    if err:
        return jsonify({"error": err}), 400
    if branch and (len(branch) > 200 or not re.fullmatch(r"[A-Za-z0-9._/-]+", branch)):
        return jsonify({"error": "invalid branch name"}), 400
    try:
        ws, stderr = workspaces_module.init_from_git(
            request.user.id, ws_id, url, branch, request.user.github_access_token,
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
    ok, output = workspaces_module.git_pull(request.user.id, ws_id, request.user.github_access_token)
    return jsonify({"ok": ok, "output": output}), 200 if ok else 400


@app.route("/api/workspaces/<ws_id>/repository", methods=["DELETE"])
@login_required
@rate_limited("default")
def workspaces_disconnect_repository(ws_id):
    if not workspaces_module.disconnect_repository(request.user.id, ws_id):
        return jsonify({"error": "git repository workspace not found"}), 404
    active = workspaces_module.get_active_workspace(request.user.id)
    return jsonify({"ok": True, "active_id": active.id if active else None})


# ===================================================================
# Shell, Tree, File
# ===================================================================


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

    import os as _os
    from stupidex.shell_executor import APPROVAL_REQUIRED as _APPROVAL_REQUIRED

    mode = request.user.shell_approval_mode or _os.environ.get("STUPIDEX_SHELL_APPROVAL_MODE", "auto")
    if mode == "ask":
        cmd_lower = cmd.lower()
        if any(cmd_lower.startswith(a.lower()) for a in _APPROVAL_REQUIRED):
            if not data.get("approved"):
                return jsonify({
                    "output": f"AVISO: O comando '{cmd}' requer confirmação.",
                    "code": 0,
                    "approval_required": True,
                    "tree_changed": False,
                })

    from stupidex.llm.tools import run_shell as run_shell_tool
    output = run_shell_tool(cmd, cwd=str(ws_path), workspace_root=str(ws_path), github_token=request.user.github_access_token)
    tree_changed = "stdout:" in output or "stderr:" in output
    return jsonify({"output": output, "code": 0, "tree_changed": tree_changed})


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
    if not path_within(target, ws_path):
        return jsonify({"error": "invalid path"}), 400
    if not target.is_file():
        return jsonify({"error": "not a file"}), 404
    if target.stat().st_size > 1 * 1024 * 1024:
        return jsonify({"error": "file too large (max 1 MB)"}), 413
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return jsonify({"error": "binary file"}), 415
    return jsonify({"path": rel, "content": content, "size": target.stat().st_size})


# ===================================================================
# Workspace context (singular /api/workspace/ routes)
# ===================================================================


@app.route("/api/workspace/debug", methods=["GET"])
@login_required
def workspace_debug():
    from ..llm.handle_input import _workspace_file_tree, _workspace_files_list, _workspace_summary, _workspace_context_for_llm
    user_id = request.user.id
    return jsonify({
        "user_id": user_id,
        "summary": _workspace_summary(user_id),
        "tree": _workspace_file_tree(user_id),
        "key_files": [{"path": f["path"], "size": f["size"], "preview_length": len(f["preview"])} for f in _workspace_files_list(user_id)],
        "full_context_length": len(_workspace_context_for_llm(user_id)),
        "full_context_preview": _workspace_context_for_llm(user_id)[:2000],
    })


@app.route("/api/workspace/context", methods=["GET"])
@login_required
@rate_limited("default")
def workspace_context():
    from ..llm.handle_input import _workspace_context_for_llm
    active = workspaces_module.get_active_workspace(request.user.id)
    if not active:
        return jsonify({"active": False, "workspace": None, "context": ""})
    return jsonify({"active": True, "workspace": active.to_dict(), "context": _workspace_context_for_llm(request.user.id)})
