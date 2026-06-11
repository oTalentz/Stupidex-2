"""Per-user workspace management.

Each user gets their own workspace directory:
  ~/.stupidex/workspaces/<user_id>/<ws_id>/

Workspaces can be populated by:
  - Uploading files (POST /api/workspaces/<id>/upload)
  - Cloning a git repository (POST /api/workspaces/<id>/clone)
  - Direct write/edit via the agent's tools (sandboxed to the workspace)
"""
import json
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import DATA_DIR

WORKSPACES_BASE = DATA_DIR / "workspaces"


def _user_dir(user_id: str) -> Path:
    return WORKSPACES_BASE / user_id


@dataclass
class Workspace:
    id: str
    name: str
    source: str
    git_url: str | None
    git_branch: str | None
    created_at: float
    last_activity: float
    size_bytes: int
    file_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "source": self.source,
            "git_url": self.git_url, "git_branch": self.git_branch,
            "created_at": self.created_at, "last_activity": self.last_activity,
            "size_bytes": self.size_bytes, "file_count": self.file_count,
        }


def _meta_path(user_id: str, ws_id: str) -> Path:
    return _user_dir(user_id) / ws_id / ".stupidex.json"


def _read_meta(user_id: str, ws_id: str) -> dict | None:
    p = _meta_path(user_id, ws_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_meta(user_id: str, ws: Workspace) -> None:
    _meta_path(user_id, ws.id).write_text(
        json.dumps(ws.to_dict(), indent=2), encoding="utf-8")


def _dir_stats(root: Path) -> tuple[int, int]:
    total_size, count = 0, 0
    for p in root.rglob("*"):
        if any(part.startswith(".git") for part in p.parts):
            continue
        if p.is_file():
            try:
                total_size += p.stat().st_size
                count += 1
            except OSError:
                pass
    return total_size, count


def _default_meta(ws_id: str) -> dict:
    now = time.time()
    return {
        "id": ws_id, "name": "Workspace", "source": "empty",
        "git_url": None, "git_branch": None, "created_at": now,
        "last_activity": now, "size_bytes": 0, "file_count": 0,
    }


# ============================================================
# CRUD
# ============================================================

def create_empty(user_id: str, name: str) -> Workspace:
    ws_id = uuid.uuid4().hex[:12]
    ws_dir = _user_dir(user_id) / ws_id
    ws_dir.mkdir(parents=True, exist_ok=True)
    ws = Workspace(id=ws_id, name=name or "Workspace", source="empty",
                   git_url=None, git_branch=None, created_at=time.time(),
                   last_activity=time.time(), size_bytes=0, file_count=0)
    _write_meta(user_id, ws)
    return ws


def list_workspaces(user_id: str) -> list[Workspace]:
    root = _user_dir(user_id)
    if not root.exists():
        return []
    out: list[Workspace] = []
    for p in root.iterdir():
        if not p.is_dir():
            continue
        meta = _read_meta(user_id, p.name)
        if not meta:
            continue
        size, count = _dir_stats(p)
        meta["size_bytes"] = size
        meta["file_count"] = count
        out.append(Workspace(**meta))
    out.sort(key=lambda w: w.last_activity, reverse=True)
    return out


def get_workspace(user_id: str, ws_id: str) -> Workspace | None:
    meta = _read_meta(user_id, ws_id)
    if not meta:
        return None
    p = _user_dir(user_id) / ws_id
    if not p.exists():
        return None
    size, count = _dir_stats(p)
    meta["size_bytes"] = size
    meta["file_count"] = count
    return Workspace(**meta)


def get_active_workspace(user_id: str) -> Workspace | None:
    cfg_path = _user_dir(user_id) / ".active_workspace"
    if cfg_path.exists():
        try:
            active_id = cfg_path.read_text(encoding="utf-8").strip()
            if active_id:
                return get_workspace(user_id, active_id)
        except Exception:
            pass
    ws_list = list_workspaces(user_id)
    return ws_list[0] if ws_list else None


def set_active_workspace(user_id: str, ws_id: str) -> bool:
    if not (_user_dir(user_id) / ws_id).exists():
        return False
    _user_dir(user_id).mkdir(parents=True, exist_ok=True)
    (_user_dir(user_id) / ".active_workspace").write_text(ws_id, encoding="utf-8")
    return True


def delete_workspace(user_id: str, ws_id: str) -> bool:
    p = _user_dir(user_id) / ws_id
    if not p.exists():
        return False
    active = get_active_workspace(user_id)
    if active and active.id == ws_id:
        (_user_dir(user_id) / ".active_workspace").unlink(missing_ok=True)
    shutil.rmtree(p, ignore_errors=True)
    return True


def touch(user_id: str, ws_id: str) -> None:
    meta = _read_meta(user_id, ws_id)
    if not meta:
        return
    meta["last_activity"] = time.time()
    p = _user_dir(user_id) / ws_id
    size, count = _dir_stats(p)
    meta["size_bytes"] = size
    meta["file_count"] = count
    _write_meta(user_id, Workspace(**meta))


def workspace_path(user_id: str, ws_id: str) -> Path | None:
    p = _user_dir(user_id) / ws_id
    return p if p.exists() else None


# ============================================================
# Sources
# ============================================================

def init_from_upload(user_id: str, ws_id: str) -> Workspace:
    meta = _read_meta(user_id, ws_id)
    if not meta:
        raise ValueError("workspace not found")
    meta["source"] = "upload"
    _write_meta(user_id, Workspace(**meta))
    return Workspace(**meta)


def init_from_git(user_id: str, ws_id: str, url: str, branch: str | None) -> tuple[Workspace, str]:
    ws_path = _user_dir(user_id) / ws_id
    if not ws_path.exists():
        raise ValueError("workspace not found")
    if _has_git():
        return _git_clone(user_id, ws_id, url, branch)
    return _download_archive(user_id, ws_id, url, branch)


def _has_git() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def _git_clone(user_id: str, ws_id: str, url: str, branch: str | None) -> tuple[Workspace, str]:
    ws_path = _user_dir(user_id) / ws_id
    meta_file = ws_path / ".stupidex.json"
    meta_backup: Path | None = None
    if meta_file.exists():
        meta_backup = ws_path.parent / f".stupidex.tmp.{ws_id}"
        meta_file.rename(meta_backup)
    try:
        cmd = ["git", "clone"]
        if branch:
            cmd += ["-b", branch]
        cmd += [url, str(ws_path)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            for child in ws_path.iterdir():
                shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink(missing_ok=True)
            raise RuntimeError(f"git clone failed (exit {proc.returncode}): {proc.stderr.strip()}")
        meta = _read_meta(user_id, ws_id)
        if meta is None:
            meta = _default_meta(ws_id)
        meta["source"] = "git"
        meta["git_url"] = url
        meta["git_branch"] = branch
        ws_obj = Workspace(**meta)
        _write_meta(user_id, ws_obj)
        return ws_obj, proc.stderr
    finally:
        if meta_backup is not None and meta_backup.exists():
            meta_backup.rename(meta_file) if not meta_file.exists() else meta_backup.unlink()


def _download_archive(user_id: str, ws_id: str, url: str, branch: str | None) -> tuple[Workspace, str]:
    import tempfile
    import urllib.request
    import zipfile

    ws_path = _user_dir(user_id) / ws_id
    meta_file = ws_path / ".stupidex.json"
    meta_backup: Path | None = None
    if meta_file.exists():
        meta_backup = ws_path.parent / f".stupidex.tmp.{ws_id}"
        meta_file.rename(meta_backup)

    archive_url = _archive_url_for(url, branch)
    if not archive_url:
        if meta_backup is not None and meta_backup.exists():
            meta_backup.rename(meta_file) if not meta_file.exists() else meta_backup.unlink()
        raise RuntimeError(
            f"git CLI not available and URL {url!r} not recognized as GitHub/GitLab. "
            "Pre-install git on the host or use a GitHub/GitLab URL."
        )

    try:
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        urllib.request.urlretrieve(archive_url, tmp_path)
        with zipfile.ZipFile(tmp_path) as zf:
            names = zf.namelist()
            if not names:
                raise RuntimeError("archive empty")
            top = names[0].split("/")[0]
            for member in names:
                if member.endswith("/") or ".." in Path(member).parts:
                    continue
                rel = member[len(top) + 1:] if member.startswith(top + "/") else member
                if not rel:
                    continue
                dest = ws_path / rel
                if not str(dest.resolve()).startswith(str(ws_path.resolve())):
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(dest, "wb") as out:
                    out.write(src.read())
        tmp_path.unlink(missing_ok=True)
        meta = _read_meta(user_id, ws_id)
        if meta is None:
            meta = _default_meta(ws_id)
        meta["source"] = "git"
        meta["git_url"] = url
        meta["git_branch"] = branch
        ws_obj = Workspace(**meta)
        _write_meta(user_id, ws_obj)
        return ws_obj, f"downloaded {archive_url}"
    finally:
        if meta_backup is not None and meta_backup.exists():
            meta_backup.rename(meta_file) if not meta_file.exists() else meta_backup.unlink()


def _archive_url_for(url: str, branch: str | None) -> str | None:
    from urllib.parse import urlparse
    ref = branch or "HEAD"
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    path = parsed.path.rstrip("/")
    if not path.endswith(".git"):
        path += ".git"
    if host in ("github.com", "www.github.com"):
        return f"https://codeload.github.com{path[:-4]}/zip/refs/heads/{ref}"
    if host in ("gitlab.com", "www.gitlab.com"):
        return f"https://gitlab.com{path[:-4]}/-/archive/{ref}.zip"
    return None


def git_pull(user_id: str, ws_id: str) -> tuple[bool, str]:
    ws = get_workspace(user_id, ws_id)
    if not ws or ws.source != "git" or not ws.git_url:
        return False, "workspace is not a git repository"
    try:
        import urllib.request
        import zipfile

        ws_path = _user_dir(user_id) / ws_id
        archive_url = _archive_url_for(ws.git_url, ws.git_branch or None)
        if not archive_url:
            return False, "cannot determine archive URL"
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        urllib.request.urlretrieve(archive_url, tmp_path)
        for child in ws_path.iterdir():
            if child.name == ".stupidex.json":
                continue
            shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink(missing_ok=True)
        with zipfile.ZipFile(tmp_path) as zf:
            names = zf.namelist()
            top = names[0].split("/")[0] if names else ""
            for member in names:
                if member.endswith("/") or ".." in Path(member).parts:
                    continue
                rel = member[len(top) + 1:] if member.startswith(top + "/") else member
                if not rel:
                    continue
                dest = ws_path / rel
                if not str(dest.resolve()).startswith(str(ws_path.resolve())):
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(dest, "wb") as out:
                    out.write(src.read())
        tmp_path.unlink(missing_ok=True)
        touch(user_id, ws_id)
        return True, f"re-downloaded {archive_url}"
    except Exception as exc:
        return False, f"pull failed: {exc}"


# ============================================================
# File tree
# ============================================================

def file_tree(user_id: str, ws_id: str, max_depth: int = 6) -> list[dict]:
    root = _user_dir(user_id) / ws_id
    if not root.exists():
        return []
    out: list[dict] = []

    def _collect_children(p: Path, rel: str, depth: int) -> list[dict]:
        if depth > max_depth:
            return []
        result: list[dict] = []
        try:
            entries = sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except (PermissionError, OSError):
            return result
        for entry in entries:
            if entry.name == ".stupidex.json":
                continue
            if any(part.startswith(".git") for part in entry.parts):
                continue
            entry_rel = f"{rel}/{entry.name}" if rel else entry.name
            if entry.is_dir():
                result.append({
                    "path": entry_rel, "name": entry.name, "type": "directory",
                    "children": _collect_children(entry, entry_rel, depth + 1),
                })
            else:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                result.append({
                    "path": entry_rel, "name": entry.name, "type": "file", "size": size,
                })
        return result

    return _collect_children(root, "", 0)
