"""Per-user workspace management.

Each user gets their own workspace directory:
  ~/.stupidex/workspaces/<user_id>/<ws_id>/

Workspaces can be populated by:
  - Uploading files (POST /api/workspaces/<id>/upload)
  - Cloning a git repository (POST /api/workspaces/<id>/clone)
  - Direct write/edit via the agent's tools (sandboxed to the workspace)

GitHub token priority:
  1. User's personal OAuth token (per-user)
  2. Server's GITHUB_PAT (Personal Access Token) for system-wide access
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import DATA_DIR

WORKSPACES_BASE = DATA_DIR / "workspaces"
MAX_WORKSPACE_BYTES = 200 * 1024 * 1024
MAX_WORKSPACE_FILES = 10_000
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_FILE_BYTES = 20 * 1024 * 1024
_WS_ID_RX = re.compile(r"^[0-9a-f]{12}$")
_ARCHIVE_HOSTS = {
    "api.github.com",
    "codeload.github.com",
    "github.com",
    "gitlab.com",
    "www.gitlab.com",
}


class RepositoryAccessError(RuntimeError):
    pass


class _SafeArchiveRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlparse(newurl)
        if (
            target.scheme != "https"
            or (target.hostname or "").lower() not in _ARCHIVE_HOSTS
        ):
            raise RuntimeError("archive redirect left the allowed hosts")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        source_host = (urllib.parse.urlparse(req.full_url).hostname or "").lower()
        target_host = (target.hostname or "").lower()
        if source_host != target_host:
            redirected.remove_header("Authorization")
        return redirected


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


def _meta_path(user_id: str, ws_id: str) -> Path:
    if not _WS_ID_RX.fullmatch(ws_id or ""):
        return _user_dir(user_id) / ".invalid" / ".stupidex.json"
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
        json.dumps(ws.to_dict(), indent=2), encoding="utf-8"
    )


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
        "id": ws_id,
        "name": "Workspace",
        "source": "empty",
        "git_url": None,
        "git_branch": None,
        "created_at": now,
        "last_activity": now,
        "size_bytes": 0,
        "file_count": 0,
    }


# ============================================================
# CRUD
# ============================================================


def create_empty(user_id: str, name: str) -> Workspace:
    ws_id = uuid.uuid4().hex[:12]
    ws_dir = _user_dir(user_id) / ws_id
    ws_dir.mkdir(parents=True, exist_ok=True)
    ws = Workspace(
        id=ws_id,
        name=name or "Workspace",
        source="empty",
        git_url=None,
        git_branch=None,
        created_at=time.time(),
        last_activity=time.time(),
        size_bytes=0,
        file_count=0,
    )
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
    if not _WS_ID_RX.fullmatch(ws_id or ""):
        return False
    if not (_user_dir(user_id) / ws_id).exists():
        return False
    _user_dir(user_id).mkdir(parents=True, exist_ok=True)
    (_user_dir(user_id) / ".active_workspace").write_text(ws_id, encoding="utf-8")
    return True


def delete_workspace(user_id: str, ws_id: str) -> bool:
    if not _WS_ID_RX.fullmatch(ws_id or ""):
        return False
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
    if not _WS_ID_RX.fullmatch(ws_id or ""):
        return None
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


def init_from_git(
    user_id: str,
    ws_id: str,
    url: str,
    branch: str | None,
    github_token: str = "",
) -> tuple[Workspace, str]:
    ws_path = workspace_path(user_id, ws_id)
    if ws_path is None:
        raise ValueError("workspace not found")

    # Token priority: user token first, then server PAT
    effective_token = github_token or os.environ.get("GITHUB_PAT", "")

    ws_obj, msg = _download_archive(user_id, ws_id, url, branch, effective_token)
    _ensure_git_repo(ws_path)
    return ws_obj, msg


def _download_archive(
    user_id: str,
    ws_id: str,
    url: str,
    branch: str | None,
    github_token: str = "",
) -> tuple[Workspace, str]:
    ws_path = _user_dir(user_id) / ws_id
    meta_file = ws_path / ".stupidex.json"
    original_meta = _read_meta(user_id, ws_id)
    meta_backup: Path | None = None
    if meta_file.exists():
        meta_backup = ws_path.parent / f".stupidex.tmp.{ws_id}"
        meta_file.rename(meta_backup)

    archive_url = _archive_url_for(url, branch, github_token)
    if not archive_url:
        if meta_backup is not None and meta_backup.exists():
            meta_backup.rename(
                meta_file
            ) if not meta_file.exists() else meta_backup.unlink()
        raise RuntimeError(
            f"git CLI not available and URL {url!r} not recognized as GitHub/GitLab. "
            "Pre-install git on the host or use a GitHub/GitLab URL."
        )

    try:
        tmp_path = _download_archive_file(archive_url, github_token)
        stage = Path(tempfile.mkdtemp(prefix=f".{ws_id}-", dir=ws_path.parent))
        try:
            _extract_archive(tmp_path, stage)
            for child in stage.iterdir():
                shutil.move(str(child), str(ws_path / child.name))
        finally:
            tmp_path.unlink(missing_ok=True)
            shutil.rmtree(stage, ignore_errors=True)
        meta = original_meta or _default_meta(ws_id)
        meta["source"] = "git"
        meta["git_url"] = url
        meta["git_branch"] = branch
        meta["size_bytes"], meta["file_count"] = _dir_stats(ws_path)
        meta["last_activity"] = time.time()
        ws_obj = Workspace(**meta)
        _write_meta(user_id, ws_obj)
        return ws_obj, f"downloaded {archive_url}"
    finally:
        if meta_backup is not None and meta_backup.exists():
            meta_backup.rename(
                meta_file
            ) if not meta_file.exists() else meta_backup.unlink()


def _ensure_git_repo(ws_path: Path) -> None:
    """Initialize a git repository in the workspace if git CLI is available."""
    git = shutil.which("git")
    if not git:
        return
    git_dir = ws_path / ".git"
    if git_dir.is_dir():
        subprocess.run(
            [git, "add", "-A"], cwd=str(ws_path), capture_output=True, timeout=30
        )
        return
    try:
        subprocess.run([git, "init"], cwd=str(ws_path), capture_output=True, timeout=15)
        subprocess.run(
            [git, "config", "user.email", "agent@stupidex.local"],
            cwd=str(ws_path),
            capture_output=True,
            timeout=15,
        )
        subprocess.run(
            [git, "config", "user.name", "Stupidex Agent"],
            cwd=str(ws_path),
            capture_output=True,
            timeout=15,
        )
        subprocess.run(
            [git, "add", "-A"], cwd=str(ws_path), capture_output=True, timeout=30
        )
        subprocess.run(
            [git, "commit", "-m", "Initial commit"],
            cwd=str(ws_path),
            capture_output=True,
            timeout=30,
        )
    except Exception:
        pass


def _github_repo_slug(url: str) -> str | None:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if (parsed.hostname or "").lower() not in ("github.com", "www.github.com"):
        return None
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) != 2:
        return None
    owner, repo = parts
    repo = repo.removesuffix(".git")
    if not owner or not repo:
        return None
    return f"{owner}/{repo}"


def _archive_url_for(
    url: str, branch: str | None, github_token: str = ""
) -> str | None:
    from urllib.parse import urlparse

    ref = urllib.parse.quote(branch or "HEAD", safe="-._/")
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    path = parsed.path.rstrip("/")
    if not path.endswith(".git"):
        path += ".git"
    if host in ("github.com", "www.github.com"):
        slug = _github_repo_slug(url)
        if not slug:
            return None
        if github_token:
            api_ref = urllib.parse.quote(branch or "HEAD", safe="-._~")
            return f"https://api.github.com/repos/{slug}/zipball/{api_ref}"
        suffix = f"refs/heads/{ref}" if branch else "HEAD"
        return f"https://codeload.github.com{path[:-4]}/zip/{suffix}"
    if host in ("gitlab.com", "www.gitlab.com"):
        return f"https://gitlab.com{path[:-4]}/-/archive/{ref}.zip"
    return None


def git_pull(user_id: str, ws_id: str, github_token: str = "") -> tuple[bool, str]:
    ws = get_workspace(user_id, ws_id)
    if not ws or ws.source != "git" or not ws.git_url:
        return False, "workspace is not a git repository"
    ws_path = _user_dir(user_id) / ws_id

    # Token priority: user token first, then server PAT
    effective_token = github_token or os.environ.get("GITHUB_PAT", "")

    # Preserve .git directory if it exists
    git_dir = ws_path / ".git"
    git_tmp = None
    if git_dir.is_dir():
        git_tmp = ws_path.parent / f".git.tmp.{ws_id}"
        try:
            shutil.copytree(git_dir, git_tmp, dirs_exist_ok=True)
        except Exception:
            git_tmp = None
    try:
        archive_url = _archive_url_for(
            ws.git_url, ws.git_branch or None, effective_token
        )
        if not archive_url:
            return False, "cannot determine archive URL"
        tmp_path = _download_archive_file(archive_url, github_token)
        stage = Path(tempfile.mkdtemp(prefix=f".{ws_id}-pull-", dir=ws_path.parent))
        try:
            _extract_archive(tmp_path, stage)
            for child in ws_path.iterdir():
                if child.name == ".stupidex.json":
                    continue
                shutil.rmtree(
                    child, ignore_errors=True
                ) if child.is_dir() else child.unlink(missing_ok=True)
            for child in stage.iterdir():
                shutil.move(str(child), str(ws_path / child.name))
        finally:
            tmp_path.unlink(missing_ok=True)
            shutil.rmtree(stage, ignore_errors=True)
        touch(user_id, ws_id)
        _ensure_git_repo(ws_path)
        return True, f"re-downloaded {archive_url}"
    except Exception as exc:
        return False, f"pull failed: {exc}"
    finally:
        if git_tmp:
            try:
                if not git_dir.is_dir() and git_tmp.is_dir():
                    shutil.copytree(git_tmp, git_dir, dirs_exist_ok=True)
                shutil.rmtree(git_tmp, ignore_errors=True)
            except Exception:
                pass


def _download_archive_file(url: str, github_token: str = "") -> Path:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Stupidex/0.1",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if github_token and urllib.parse.urlparse(url).hostname == "api.github.com":
        headers["Authorization"] = f"Bearer {github_token}"
    request = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(_SafeArchiveRedirectHandler())
    tmp_path: Path | None = None
    try:
        with opener.open(request, timeout=30) as response:
            final = urllib.parse.urlparse(response.geturl())
            if (
                final.scheme != "https"
                or (final.hostname or "").lower() not in _ARCHIVE_HOSTS
            ):
                raise RuntimeError("archive redirect left the allowed hosts")
            declared = int(response.headers.get("Content-Length") or 0)
            if declared > MAX_ARCHIVE_BYTES:
                raise RuntimeError("repository archive is too large")
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                tmp_path = Path(tmp.name)
                total = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_ARCHIVE_BYTES:
                        raise RuntimeError("repository archive is too large")
                    tmp.write(chunk)
        return tmp_path
    except urllib.error.HTTPError as exc:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        if host in {"api.github.com", "codeload.github.com"} and exc.code in {
            401,
            403,
            404,
        }:
            if github_token:
                raise RepositoryAccessError(
                    "GitHub denied repository access. Reconnect your GitHub account "
                    "and confirm that it can access this repository."
                ) from exc
            raise RepositoryAccessError(
                "Repository not found or private. Connect your GitHub account to "
                "clone private repositories."
            ) from exc
        raise
    except Exception:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise


def _extract_archive(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as zf:
        files = [info for info in zf.infolist() if not info.is_dir()]
        if not files:
            raise RuntimeError("archive empty")
        if len(files) > MAX_WORKSPACE_FILES:
            raise RuntimeError("archive contains too many files")
        expanded = sum(info.file_size for info in files)
        if expanded > MAX_WORKSPACE_BYTES:
            raise RuntimeError("expanded archive is too large")
        if any(info.file_size > MAX_FILE_BYTES for info in files):
            raise RuntimeError("archive contains an oversized file")
        if any((info.external_attr >> 16) & 0o170000 == 0o120000 for info in files):
            raise RuntimeError("archive symlinks are not allowed")

        names = [info.filename for info in files]
        top = names[0].split("/")[0] if names else ""
        for info in files:
            if info.flag_bits & 0x1:
                raise RuntimeError("encrypted archives are not allowed")
            member = info.filename.replace("\\", "/")
            if "\x00" in member or ".." in Path(member).parts:
                raise RuntimeError("archive contains an invalid path")
            rel = member[len(top) + 1 :] if member.startswith(top + "/") else member
            if not rel:
                continue
            if Path(rel).as_posix() == ".stupidex.json":
                raise RuntimeError("archive contains a reserved file name")
            target = (destination / rel).resolve()
            try:
                target.relative_to(destination.resolve())
            except ValueError as exc:
                raise RuntimeError("archive path escapes workspace") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with zf.open(info) as src, open(target, "wb") as dst:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_FILE_BYTES:
                        raise RuntimeError("archive member exceeded its declared limit")
                    dst.write(chunk)


# ============================================================
# File tree
# ============================================================


def file_tree(user_id: str, ws_id: str, max_depth: int = 6) -> list[dict]:
    root = _user_dir(user_id) / ws_id
    if not root.exists():
        return []

    def _collect_children(p: Path, rel: str, depth: int) -> list[dict]:
        if depth > max_depth:
            return []
        result: list[dict] = []
        try:
            entries = sorted(
                p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())
            )
        except (PermissionError, OSError):
            return result
        for entry in entries:
            if entry.name == ".stupidex.json":
                continue
            if any(part.startswith(".git") for part in entry.parts):
                continue
            entry_rel = f"{rel}/{entry.name}" if rel else entry.name
            if entry.is_dir():
                result.append(
                    {
                        "path": entry_rel,
                        "name": entry.name,
                        "type": "directory",
                        "children": _collect_children(entry, entry_rel, depth + 1),
                    }
                )
            else:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                result.append(
                    {
                        "path": entry_rel,
                        "name": entry.name,
                        "type": "file",
                        "size": size,
                    }
                )
        return result

    return _collect_children(root, "", 0)
