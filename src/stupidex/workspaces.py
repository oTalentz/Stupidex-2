"""Workspace management.

A workspace is a named directory under ~/.stupidex/workspaces/<id>/.
Workspaces can be populated by:
  • Uploading files (POST /api/workspaces/<id>/upload)
  • Cloning a git repository (POST /api/workspaces/<id>/clone)
  • Direct write/edit via the agent's tools

The active workspace is what the agent's tools see as their CWD.
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

WORKSPACES_DIR = DATA_DIR / "workspaces"


@dataclass
class Workspace:
    id: str
    name: str
    source: str  # "empty" | "upload" | "git"
    git_url: str | None
    git_branch: str | None
    created_at: float
    last_activity: float
    size_bytes: int
    file_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "source": self.source,
            "git_url": self.git_url,
            "git_branch": self.git_branch,
            "created_at": self.created_at,
            "last_activity": self.last_activity,
            "size_bytes": self.size_bytes,
            "file_count": self.file_count,
        }


def _meta_path(ws_id: str) -> Path:
    return WORKSPACES_DIR / ws_id / ".stupidex.json"


def _read_meta(ws_id: str) -> dict | None:
    p = _meta_path(ws_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_meta(ws: Workspace) -> None:
    _meta_path(ws.id).write_text(json.dumps(ws.to_dict(), indent=2), encoding="utf-8")


def _dir_stats(root: Path) -> tuple[int, int]:
    total_size = 0
    count = 0
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


def _make_workspace(name: str, source: str) -> Workspace:
    WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
    ws_id = uuid.uuid4().hex[:12]
    ws_dir = WORKSPACES_DIR / ws_id
    ws_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()
    ws = Workspace(
        id=ws_id, name=name, source=source, git_url=None, git_branch=None,
        created_at=now, last_activity=now, size_bytes=0, file_count=0,
    )
    _write_meta(ws)
    return ws


def create_empty(name: str) -> Workspace:
    return _make_workspace(name or "Workspace", "empty")


def list_workspaces() -> list[Workspace]:
    if not WORKSPACES_DIR.exists():
        return []
    out: list[Workspace] = []
    for p in WORKSPACES_DIR.iterdir():
        if not p.is_dir():
            continue
        meta = _read_meta(p.name)
        if not meta:
            continue
        # refresh stats
        size, count = _dir_stats(p)
        meta["size_bytes"] = size
        meta["file_count"] = count
        out.append(Workspace(**meta))
    out.sort(key=lambda w: w.last_activity, reverse=True)
    return out


def get_workspace(ws_id: str) -> Workspace | None:
    meta = _read_meta(ws_id)
    if not meta:
        return None
    p = WORKSPACES_DIR / ws_id
    if not p.exists():
        return None
    size, count = _dir_stats(p)
    meta["size_bytes"] = size
    meta["file_count"] = count
    return Workspace(**meta)


def get_active_workspace() -> Workspace | None:
    cfg_path = DATA_DIR / "active_workspace"
    if cfg_path.exists():
        try:
            active_id = cfg_path.read_text(encoding="utf-8").strip()
            if active_id:
                return get_workspace(active_id)
        except Exception:
            pass
    ws = list_workspaces()
    return ws[0] if ws else None


def set_active_workspace(ws_id: str) -> bool:
    if not (WORKSPACES_DIR / ws_id).exists():
        return False
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "active_workspace").write_text(ws_id, encoding="utf-8")
    return True


def delete_workspace(ws_id: str) -> bool:
    p = WORKSPACES_DIR / ws_id
    if not p.exists():
        return False
    if get_active_workspace() and get_active_workspace().id == ws_id:
        (DATA_DIR / "active_workspace").unlink(missing_ok=True)
    shutil.rmtree(p, ignore_errors=True)
    return True


def touch(ws_id: str) -> None:
    meta = _read_meta(ws_id)
    if not meta:
        return
    meta["last_activity"] = time.time()
    p = WORKSPACES_DIR / ws_id
    size, count = _dir_stats(p)
    meta["size_bytes"] = size
    meta["file_count"] = count
    _write_meta(Workspace(**meta))


def workspace_path(ws_id: str) -> Path | None:
    p = WORKSPACES_DIR / ws_id
    return p if p.exists() else None


# ---------- Sources ----------

def init_from_upload(ws_id: str) -> Workspace:
    """Mark a workspace as having come from an upload."""
    meta = _read_meta(ws_id)
    if not meta:
        raise ValueError("workspace not found")
    meta["source"] = "upload"
    _write_meta(Workspace(**meta))
    return Workspace(**meta)


def init_from_git(ws_id: str, url: str, branch: str | None) -> tuple[Workspace, str]:
    """Clone a repository into the workspace.

    Two strategies:
    1. If `git` is on PATH, use the real `git clone` (preserves history, submodules, LFS).
    2. Otherwise, fall back to downloading a tarball/zip via HTTPS — works on
       hosts without git CLI (e.g. Square Cloud Hobby).

    Supports GitHub URLs natively, plus any HTTPS git host that exposes a
    /archive/<ref>.tar.gz (or .zip) endpoint.
    """
    ws_path = WORKSPACES_DIR / ws_id
    if not ws_path.exists():
        raise ValueError("workspace not found")

    # If git is available, use it.
    if _has_git():
        return _git_clone(ws_id, url, branch)

    # Otherwise, try to download as a tarball/zip.
    return _download_archive(ws_id, url, branch)


def _has_git() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def _git_clone(ws_id: str, url: str, branch: str | None) -> tuple[Workspace, str]:
    ws_path = WORKSPACES_DIR / ws_id
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
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
            raise RuntimeError(f"git clone failed (exit {proc.returncode}): {proc.stderr.strip()}")
        meta = _read_meta(ws_id)
        if meta is None:
            meta = _default_meta(ws_id)
        meta["source"] = "git"
        meta["git_url"] = url
        meta["git_branch"] = branch
        ws_obj = Workspace(**meta)
        _write_meta(ws_obj)
        return ws_obj, proc.stderr
    finally:
        if meta_backup is not None and meta_backup.exists():
            if not meta_file.exists():
                meta_backup.rename(meta_file)
            else:
                meta_backup.unlink()


def _default_meta(ws_id: str) -> dict:
    return {
        "id": ws_id,
        "name": "Workspace",
        "source": "empty",
        "git_url": None,
        "git_branch": None,
        "created_at": time.time(),
        "last_activity": time.time(),
        "size_bytes": 0,
        "file_count": 0,
    }


def _download_archive(ws_id: str, url: str, branch: str | None) -> tuple[Workspace, str]:
    """Download a repo tarball/zip from GitHub or a GitLab-style host."""
    import tempfile
    import urllib.request
    import zipfile

    ws_path = WORKSPACES_DIR / ws_id
    # Move the .stupidex.json aside so the archive extract doesn't conflict
    meta_file = ws_path / ".stupidex.json"
    meta_backup: Path | None = None
    if meta_file.exists():
        meta_backup = ws_path.parent / f".stupidex.tmp.{ws_id}"
        meta_file.rename(meta_backup)

    # Build archive URL. We support github.com and gitlab.com out of the box.
    archive_url = _archive_url_for(url, branch)
    if not archive_url:
        if meta_backup is not None and meta_backup.exists():
            if not meta_file.exists():
                meta_backup.rename(meta_file)
            else:
                meta_backup.unlink()
        raise RuntimeError(
            f"git CLI is not available and URL {url!r} is not recognized as GitHub/GitLab. "
            "Pre-install git on the host or use a different URL."
        )

    try:
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        urllib.request.urlretrieve(archive_url, tmp_path)

        with zipfile.ZipFile(tmp_path) as zf:
            names = zf.namelist()
            if not names:
                raise RuntimeError("downloaded archive is empty")
            # GitHub archives have a top-level "<repo>-<ref>/" directory.
            top = names[0].split("/")[0]
            for member in names:
                if member.endswith("/"):
                    continue
                if ".." in Path(member).parts:
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

        meta = _read_meta(ws_id)
        if meta is None:
            meta = _default_meta(ws_id)
        meta["source"] = "git"
        meta["git_url"] = url
        meta["git_branch"] = branch
        ws_obj = Workspace(**meta)
        _write_meta(ws_obj)
        return ws_obj, f"downloaded {archive_url}"
    finally:
        if meta_backup is not None and meta_backup.exists():
            if not meta_file.exists():
                meta_backup.rename(meta_file)
            else:
                meta_backup.unlink()


def _archive_url_for(url: str, branch: str | None) -> str | None:
    """Return a downloadable archive URL for common git hosts, or None."""
    from urllib.parse import urlparse

    ref = branch or "HEAD"
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    path = parsed.path.rstrip("/")
    if not path.endswith(".git"):
        path = path + ".git"
    # GitHub: https://github.com/owner/repo(.git)
    if host in ("github.com", "www.github.com"):
        owner_repo = path[:-4]  # strip .git
        return f"https://codeload.github.com{owner_repo}/zip/refs/heads/{ref}"
    # GitLab: https://gitlab.com/owner/repo(.git)
    if host in ("gitlab.com", "www.gitlab.com"):
        owner_repo = path[:-4]
        return f"https://gitlab.com{owner_repo}/-/archive/{ref}.zip"
    return None

    meta = _read_meta(ws_id)
    meta["source"] = "git"
    meta["git_url"] = url
    meta["git_branch"] = branch
    size, count = _dir_stats(ws_path)
    meta["size_bytes"] = size
    meta["file_count"] = count
    ws = Workspace(**meta)
    _write_meta(ws)
    return ws, proc.stderr


def git_pull(ws_id: str) -> tuple[bool, str]:
    """Re-fetch the workspace from its source URL. Falls back to a fresh
    download when git is not available on the host."""
    ws = get_workspace(ws_id)
    if not ws:
        return False, "workspace not found"
    if ws.source != "git" or not ws.git_url:
        return False, "workspace is not a git repository"
    try:
        # Re-download from scratch into a clean directory. Simpler than
        # `git pull` and works on hosts without git CLI.
        import shutil as _sh
        import tempfile
        import urllib.request
        import zipfile

        ws_path = WORKSPACES_DIR / ws_id
        archive_url = _archive_url_for(ws.git_url, ws.git_branch or None)
        if not archive_url:
            return False, "cannot determine archive URL"

        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        urllib.request.urlretrieve(archive_url, tmp_path)
        # Remove everything except .stupidex.json
        for child in ws_path.iterdir():
            if child.name == ".stupidex.json":
                continue
            if child.is_dir():
                _sh.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        with zipfile.ZipFile(tmp_path) as zf:
            names = zf.namelist()
            top = names[0].split("/")[0] if names else ""
            for member in names:
                if member.endswith("/"):
                    continue
                if ".." in Path(member).parts:
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
        touch(ws_id)
        return True, f"re-downloaded {archive_url}"
    except Exception as exc:
        return False, f"pull failed: {exc}"


# ---------- File tree ----------

def file_tree(ws_id: str, max_depth: int = 6) -> list[dict]:
    """Return a recursive file tree for the workspace UI."""
    root = WORKSPACES_DIR / ws_id
    if not root.exists():
        return []
    out: list[dict] = []

    def walk(p: Path, rel: str, depth: int):
        if depth > max_depth:
            return
        try:
            entries = sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except (PermissionError, OSError):
            return
        for entry in entries:
            if entry.name == ".stupidex.json":
                continue
            if any(part.startswith(".git") for part in entry.parts):
                continue
            entry_rel = f"{rel}/{entry.name}" if rel else entry.name
            if entry.is_dir():
                children: list[dict] = []
                walk(entry, entry_rel, depth + 1)
                for c in children:
                    pass
                out.append({
                    "path": entry_rel,
                    "name": entry.name,
                    "type": "directory",
                    "children": _collect_children(entry, entry_rel, depth + 1, max_depth),
                })
            else:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                out.append({
                    "path": entry_rel,
                    "name": entry.name,
                    "type": "file",
                    "size": size,
                })

    def _collect_children(p: Path, rel: str, depth: int, max_depth: int) -> list[dict]:
        result: list[dict] = []
        try:
            entries = sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except (PermissionError, OSError):
            return result
        if depth > max_depth:
            return result
        for entry in entries:
            if entry.name == ".stupidex.json":
                continue
            if any(part.startswith(".git") for part in entry.parts):
                continue
            entry_rel = f"{rel}/{entry.name}"
            if entry.is_dir():
                result.append({
                    "path": entry_rel,
                    "name": entry.name,
                    "type": "directory",
                    "children": _collect_children(entry, entry_rel, depth + 1, max_depth),
                })
            else:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                result.append({
                    "path": entry_rel,
                    "name": entry.name,
                    "type": "file",
                    "size": size,
                })
        return result

    walk(root, "", 0)
    return out
