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

import base64
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
_GIT_HOSTS = {"github.com", "www.github.com", "gitlab.com", "www.gitlab.com"}


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
                active = get_workspace(user_id, active_id)
                if active:
                    return active
        except Exception:
            pass
        cfg_path.unlink(missing_ok=True)
    ws_list = list_workspaces(user_id)
    if not ws_list:
        return None
    fallback = ws_list[0]
    set_active_workspace(user_id, fallback.id)
    return fallback


def set_active_workspace(user_id: str, ws_id: str) -> bool:
    if not _WS_ID_RX.fullmatch(ws_id or ""):
        return False
    if not (_user_dir(user_id) / ws_id).exists():
        return False
    user_dir = _user_dir(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    active_file = user_dir / ".active_workspace"
    pending_file = user_dir / f".active_workspace.{uuid.uuid4().hex}.tmp"
    try:
        pending_file.write_text(ws_id, encoding="utf-8")
        pending_file.replace(active_file)
    finally:
        pending_file.unlink(missing_ok=True)
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

    if shutil.which("git"):
        try:
            ws_obj, msg = _clone_repository(user_id, ws_id, url, branch, effective_token)
        except (RepositoryAccessError, subprocess.TimeoutExpired) as exc:
            err_msg = str(exc).lower()
            if any(
                kw in err_msg
                for kw in ("dns", "getaddrinfo", "thread failed", "resolve host", "name or service", "network", "connection refused", "connection reset", "timeout")
            ):
                ws_obj, msg = _download_archive(
                    user_id, ws_id, url, branch, effective_token
                )
                _ensure_git_repo(ws_path, url, branch)
                msg = f"git clone failed ({exc}), fell back to archive download"
            else:
                raise
    else:
        ws_obj, msg = _download_archive(user_id, ws_id, url, branch, effective_token)
        _ensure_git_repo(ws_path, url, branch)
        msg = f"{msg}; git CLI unavailable, repository history was not connected"

    if not set_active_workspace(user_id, ws_obj.id):
        raise RuntimeError("repository cloned but could not be activated")
    return ws_obj, msg


def disconnect_repository(user_id: str, ws_id: str) -> bool:
    ws = get_workspace(user_id, ws_id)
    if not ws or ws.source != "git":
        return False
    return delete_workspace(user_id, ws_id)


def _git_environment(home: Path, github_token: str = "") -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(home),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_PAGER": "cat",
            "LANG": env.get("LANG", "C.UTF-8"),
            "GIT_SSL_NO_VERIFY": env.get("GIT_SSL_NO_VERIFY", ""),
            "SSL_CERT_FILE": env.get("SSL_CERT_FILE", ""),
        }
    )
    env.pop("GIT_ASKPASS", None)
    env.pop("SSH_ASKPASS", None)
    if github_token:
        credentials = base64.b64encode(
            f"x-access-token:{github_token}".encode("utf-8")
        ).decode("ascii")
        env.update(
            {
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                "GIT_CONFIG_VALUE_0": f"Authorization: Basic {credentials}",
            }
        )
    else:
        env.pop("GIT_CONFIG_COUNT", None)
        env.pop("GIT_CONFIG_KEY_0", None)
        env.pop("GIT_CONFIG_VALUE_0", None)
    return env


def _clone_repository(
    user_id: str,
    ws_id: str,
    url: str,
    branch: str | None,
    github_token: str = "",
) -> tuple[Workspace, str]:
    parsed = urllib.parse.urlparse(url)
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() not in _GIT_HOSTS
        or parsed.username
        or parsed.password
    ):
        raise ValueError("only credential-free HTTPS GitHub/GitLab URLs are allowed")

    ws_path = workspace_path(user_id, ws_id)
    if ws_path is None:
        raise ValueError("workspace not found")
    git = shutil.which("git")
    if not git:
        raise RuntimeError("git CLI is not installed")

    stage = Path(tempfile.mkdtemp(prefix=f".{ws_id}-clone-", dir=ws_path.parent))
    clone_path = stage / "repository"
    cmd = [
        git,
        "-c",
        f"core.hooksPath={os.devnull}",
        "clone",
        "--no-recurse-submodules",
    ]
    if branch:
        cmd.extend(["--branch", branch, "--single-branch"])
    cmd.extend([url, str(clone_path)])
    try:
        result = subprocess.run(
            cmd,
            cwd=str(stage),
            capture_output=True,
            text=True,
            timeout=180,
            env=_git_environment(ws_path, github_token),
            shell=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "git clone failed").strip()
            raise RepositoryAccessError(detail[:2000])

        size, count = _dir_stats(clone_path)
        if size > MAX_WORKSPACE_BYTES or count > MAX_WORKSPACE_FILES:
            raise RuntimeError("repository exceeds workspace limits")

        for child in ws_path.iterdir():
            if child.name == ".stupidex.json":
                continue
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        for child in clone_path.iterdir():
            shutil.move(str(child), str(ws_path / child.name))

        subprocess.run(
            [git, "config", "user.email", "agent@stupidex.local"],
            cwd=str(ws_path),
            capture_output=True,
            timeout=15,
            env=_git_environment(ws_path),
        )
        subprocess.run(
            [git, "config", "user.name", "Stupidex Agent"],
            cwd=str(ws_path),
            capture_output=True,
            timeout=15,
            env=_git_environment(ws_path),
        )

        meta = _read_meta(user_id, ws_id) or _default_meta(ws_id)
        meta.update(
            {
                "source": "git",
                "git_url": url,
                "git_branch": branch,
                "size_bytes": size,
                "file_count": count,
                "last_activity": time.time(),
            }
        )
        ws_obj = Workspace(**meta)
        _write_meta(user_id, ws_obj)
        return ws_obj, (result.stderr or "repository cloned with git").strip()
    finally:
        shutil.rmtree(stage, ignore_errors=True)


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


def _ensure_git_repo(
    ws_path: Path, remote_url: str = "", branch: str | None = None
) -> None:
    """Initialize a git repository in the workspace if git CLI is available."""
    git = shutil.which("git")
    if not git:
        return
    git_dir = ws_path / ".git"
    if git_dir.is_dir():
        if remote_url:
            updated = subprocess.run(
                [git, "remote", "set-url", "origin", remote_url],
                cwd=str(ws_path),
                capture_output=True,
                timeout=15,
            )
            if updated.returncode != 0:
                subprocess.run(
                    [git, "remote", "add", "origin", remote_url],
                    cwd=str(ws_path),
                    capture_output=True,
                    timeout=15,
                )
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
        if remote_url:
            subprocess.run(
                [git, "remote", "add", "origin", remote_url],
                cwd=str(ws_path),
                capture_output=True,
                timeout=15,
            )
        if branch:
            subprocess.run(
                [git, "branch", "-M", branch],
                cwd=str(ws_path),
                capture_output=True,
                timeout=15,
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
    path = parsed.path.rstrip("/").removesuffix(".git")
    if host in ("github.com", "www.github.com"):
        slug = _github_repo_slug(url)
        if not slug:
            return None
        if github_token:
            api_ref = urllib.parse.quote(branch or "HEAD", safe="-._~")
            return f"https://api.github.com/repos/{slug}/zipball/{api_ref}"
        suffix = f"refs/heads/{ref}" if branch else "HEAD"
        return f"https://codeload.github.com{path}/zip/{suffix}"
    if host in ("gitlab.com", "www.gitlab.com"):
        return f"https://gitlab.com{path}/-/archive/{ref}.zip"
    return None


def git_pull(user_id: str, ws_id: str, github_token: str = "") -> tuple[bool, str]:
    ws = get_workspace(user_id, ws_id)
    if not ws or ws.source != "git" or not ws.git_url:
        return False, "workspace is not a git repository"
    ws_path = _user_dir(user_id) / ws_id

    # Token priority: user token first, then server PAT
    effective_token = github_token or os.environ.get("GITHUB_PAT", "")

    git = shutil.which("git")
    git_dir = ws_path / ".git"
    if git and git_dir.is_dir():
        probe = subprocess.run(
            [git, "rev-parse", "--is-inside-work-tree"],
            cwd=str(ws_path),
            capture_output=True,
            text=True,
            timeout=15,
            env=_git_environment(ws_path),
        )
        if probe.returncode == 0:
            result = subprocess.run(
                [
                    git,
                    "-c",
                    f"core.hooksPath={os.devnull}",
                    "pull",
                    "--ff-only",
                ],
                cwd=str(ws_path),
                capture_output=True,
                text=True,
                timeout=120,
                env=_git_environment(ws_path, effective_token),
                shell=False,
            )
            output = "\n".join(
                part
                for part in ((result.stdout or "").strip(), (result.stderr or "").strip())
                if part
            )
            if result.returncode != 0:
                return False, output or "git pull failed"
            touch(user_id, ws_id)
            return True, output or "Already up to date."

    # Preserve .git directory if it exists
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
        tmp_path = _download_archive_file(archive_url, effective_token)
        stage = Path(tempfile.mkdtemp(prefix=f".{ws_id}-pull-", dir=ws_path.parent))
        try:
            _extract_archive(tmp_path, stage)
            for child in ws_path.iterdir():
                if child.name in {".stupidex.json", ".git"}:
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
        _ensure_git_repo(ws_path, ws.git_url, ws.git_branch or None)
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
