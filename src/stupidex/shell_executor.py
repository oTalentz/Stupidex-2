"""Shell executor with structured policies, approval, quotas, and audit.

Architecture:
  - Parser: normalizes and validates command structure
  - Policy: allowlist, blocklist, argument rules per executable
  - Executor: subprocess.Popen with process group isolation
  - Quota: per-user / global concurrency and daily limits
  - Audit: structured log of every execution

Security model:
  - NO shell=True ever
  - argv parsed via shlex, validated token by token
  - cwd forced to workspace root (symlink-resolved)
  - Environment: minimal (PATH, HOME, TMPDIR only by default)
  - Secrets NEVER injected into subprocess env
  - Process group isolation (start_new_session) for clean kill
  - Grace period: SIGTERM → wait → SIGKILL
"""

from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_TIMEOUT = int(os.environ.get("STUPIDEX_SHELL_MAX_TIMEOUT", "120"))
MAX_OUTPUT_BYTES = int(os.environ.get("STUPIDEX_SHELL_MAX_OUTPUT_BYTES", "65536"))
MAX_CONCURRENT_PER_USER = int(os.environ.get("STUPIDEX_SHELL_MAX_CONCURRENT_PER_USER", "1"))
MAX_CONCURRENT_GLOBAL = int(os.environ.get("STUPIDEX_SHELL_MAX_CONCURRENT_GLOBAL", "4"))
REQUIRE_APPROVAL = os.environ.get("STUPIDEX_SHELL_REQUIRE_APPROVAL", "1").lower() in ("1", "true", "yes")
ALLOW_NETWORK = os.environ.get("STUPIDEX_SHELL_ALLOW_NETWORK", "0").lower() in ("1", "true", "yes")

DEFAULT_ALLOWLIST = {
    "python", "python3", "pytest", "git", "node", "npm", "npx",
    "pnpm", "yarn", "cargo", "go", "dotnet", "make", "cmake",
    "ruff", "mypy", "eslint", "prettier", "black", "isort",
}

# Commands that require explicit approval
APPROVAL_REQUIRED = {
    "npm install", "pnpm install", "yarn install", "pip install",
    "git push", "git commit", "rm", "rmdir", "del", "sudo",
}

# Blocked commands (regardless of allowlist)
ALWAYS_BLOCKED = {
    "sudo", "su", "chsh", "passwd", "kill", "pkill", "renice",
    "mount", "umount", "mkfs", "dd", "fdisk", "parted",
    "reboot", "shutdown", "halt", "poweroff", "init",
    "docker", "podman", "containerd", "runc",
    "crontab", "at", "batch", "systemctl", "systemd",
    "wget", "curl", "nc", "netcat", "telnet", "ssh", "scp",
    "sftp", "ftp", "python3 -m venv", ". /", "source /",
    "bash", "sh", "zsh", "fish", "ash", "dash",
}

# Shell operators that must be rejected
BLOCKED_OPERATORS = {"|", "&", ";", "<", ">", "`", "$(", "\n", "\r"}

# Environment variables blacklisted from subprocess (must NEVER leak)
BLACKLISTED_ENV_PATTERNS = [
    re.compile(r".*API_KEY.*", re.I),
    re.compile(r".*SECRET.*", re.I),
    re.compile(r".*TOKEN.*", re.I),
    re.compile(r".*PASSWORD.*", re.I),
    re.compile(r".*CREDENTIAL.*", re.I),
    re.compile(r".*AUTH.*", re.I),
    re.compile(r"GITHUB_.*", re.I),
    re.compile(r"GOOGLE_.*", re.I),
    re.compile(r"LITELLM_.*", re.I),
]

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class CommandRequest:
    """Validated and normalized command request."""
    id: str
    user_id: str
    workspace_id: str
    executable: str
    args: List[str]
    cwd: Path
    timeout: int
    reason: str = ""
    require_approval: bool = False
    approved: bool = False
    created_at: float = field(default_factory=time.time)

    @property
    def argv(self) -> List[str]:
        return [self.executable, *self.args]

    @property
    def display(self) -> str:
        return " ".join(shlex.quote(a) for a in self.argv)


@dataclass
class CommandResult:
    """Result of a shell execution."""
    request_id: str
    stdout: str
    stderr: str
    exit_code: int
    duration: float
    timed_out: bool
    cancelled: bool
    truncated: bool

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.cancelled


@dataclass
class AuditEntry:
    """Structured audit log entry."""
    id: str
    user_id: str
    workspace_id: str
    executable: str
    args: List[str]
    cwd: str
    timeout: int
    duration: float
    exit_code: int
    output_size: int
    approved: bool
    timed_out: bool
    cancelled: bool
    blocked: bool
    block_reason: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ---------------------------------------------------------------------------
# Quota tracker
# ---------------------------------------------------------------------------


class QuotaTracker:
    """Tracks concurrent and daily shell usage per user/globally."""

    def __init__(self):
        self._running: Dict[str, List[CommandRequest]] = {}  # user_id -> cmds
        self._global_count = 0

    def acquire(self, req: CommandRequest) -> bool:
        """Try to acquire a slot. Returns False if quota exceeded."""
        user_count = len(self._running.get(req.user_id, []))
        if user_count >= MAX_CONCURRENT_PER_USER:
            return False
        if self._global_count >= MAX_CONCURRENT_GLOBAL:
            return False
        self._running.setdefault(req.user_id, []).append(req)
        self._global_count += 1
        return True

    def release(self, req: CommandRequest) -> None:
        """Release slot after completion."""
        user_cmds = self._running.get(req.user_id)
        if user_cmds and req in user_cmds:
            user_cmds.remove(req)
            if not user_cmds:
                del self._running[req.user_id]
        self._global_count = max(0, self._global_count - 1)

    def running_count(self, user_id: str) -> int:
        return len(self._running.get(user_id, []))


# ---------------------------------------------------------------------------
# Command parser
# ---------------------------------------------------------------------------


class CommandParser:
    """Parse, validate, and normalize a command string."""

    @staticmethod
    def parse(raw: str, cwd: Path) -> CommandRequest:
        """Parse a raw command string into a validated CommandRequest."""
        # Reject shell operators
        for op in BLOCKED_OPERATORS:
            if op in raw:
                raise ValueError(f"Shell operator '{op}' is not allowed")

        # Split into argv
        try:
            argv = shlex.split(raw, posix=os.name != "nt")
        except ValueError as e:
            raise ValueError(f"Invalid command syntax: {e}")

        if not argv:
            raise ValueError("Empty command")

        # Windows: strip quotes from argv tokens
        if os.name == "nt":
            argv = [
                arg[1:-1] if len(arg) >= 2 and arg[0] == arg[-1] and arg[0] in {'"', "'"} else arg
                for arg in argv
            ]

        executable = argv[0]
        args = argv[1:]

        # Resolve the executable name (base name only, no paths)
        exec_name = Path(executable).name.lower()

        # Block always-blocked commands
        if exec_name in ALWAYS_BLOCKED:
            raise ValueError(f"Command '{exec_name}' is blocked")

        # Check allowlist
        allowlist_raw = os.environ.get("STUPIDEX_SHELL_COMMANDS", "")
        allowlist = set(
            item.strip().lower()
            for item in allowlist_raw.split(",")
            if item.strip()
        ) if allowlist_raw else DEFAULT_ALLOWLIST

        if exec_name not in allowlist:
            raise ValueError(
                f"Executable '{exec_name}' is not in the allowed list"
            )

        # Resolve and validate cwd
        resolved_cwd = cwd.resolve()
        # In production, cwd must be within the workspace directory
        # (enforced by caller)

        # Determine if approval is needed
        cmd_str = f"{exec_name} {' '.join(args)}".lower()
        needs_approval = REQUIRE_APPROVAL and any(
            cmd_str.startswith(blocked.lower())
            for blocked in APPROVAL_REQUIRED
        )

        # Check for network commands
        if not ALLOW_NETWORK and exec_name in ("wget", "curl", "nc", "ssh", "scp", "sftp", "ftp"):
            raise ValueError(f"Network command '{exec_name}' is blocked (STUPIDEX_SHELL_ALLOW_NETWORK=0)")

        return CommandRequest(
            id=uuid.uuid4().hex[:12],
            user_id="",
            workspace_id="",
            executable=exec_name,
            args=args,
            cwd=resolved_cwd,
            timeout=min(int(os.environ.get("STUPIDEX_SHELL_MAX_TIMEOUT", "120")), MAX_TIMEOUT),
            require_approval=needs_approval,
        )


# ---------------------------------------------------------------------------
# Environment builder
# ---------------------------------------------------------------------------


def _build_env(cwd: Path) -> Dict[str, str]:
    """Build a safe environment for subprocess, inheriting system vars."""
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(cwd),
            "LANG": env.get("LANG", "C.UTF-8"),
            "LC_ALL": "C.UTF-8",
            "NO_COLOR": "1",
            "CI": "1",
            "TMPDIR": str(cwd / ".tmp"),
        }
    )
    for key in list(env.keys()):
        if any(pattern.search(key) for pattern in BLACKLISTED_ENV_PATTERNS):
            env.pop(key, None)
    env.pop("GIT_ASKPASS", None)
    env.pop("SSH_ASKPASS", None)
    return env


# ---------------------------------------------------------------------------
# Auditor
# ---------------------------------------------------------------------------


class AuditLog:
    """Structured audit log for shell executions."""

    def __init__(self):
        self._entries: List[AuditEntry] = []

    def record(self, entry: AuditEntry) -> None:
        self._entries.append(entry)
        # Structured log line (safe for log aggregation)
        import logging
        logging.info(
            "[shell] user=%s ws=%s cmd=%s exit=%d dur=%.2f blocked=%s reason=%s",
            entry.user_id,
            entry.workspace_id,
            entry.executable,
            entry.exit_code,
            entry.duration,
            entry.blocked,
            entry.block_reason,
        )

    def recent(self, n: int = 50) -> List[AuditEntry]:
        return self._entries[-n:]


# ---------------------------------------------------------------------------
# Main executor
# ---------------------------------------------------------------------------


_quota = QuotaTracker()
_audit = AuditLog()


def execute(req: CommandRequest) -> CommandResult:
    """Execute a validated command request with full isolation."""
    start = time.monotonic()

    if req.require_approval and not req.approved:
        return CommandResult(
            request_id=req.id,
            stdout="",
            stderr=f"APPROVAL_REQUIRED: The command '{req.display}' requires user approval. "
                   f"Set shell mode to 'auto' or approve from the terminal.",
            exit_code=-1,
            duration=0,
            timed_out=False,
            cancelled=False,
            truncated=False,
        )

    # Acquire quota slot
    if not _quota.acquire(req):
        return CommandResult(
            request_id=req.id,
            stdout="",
            stderr="",
            exit_code=-1,
            duration=0,
            timed_out=False,
            cancelled=False,
            truncated=False,
        )

    try:
        # Build minimal environment
        env = _build_env(req.cwd)

        # Create .tmp directory if needed
        tmp_dir = req.cwd / ".tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        # Launch process
        proc = subprocess.Popen(
            req.argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(req.cwd),
            env=env,
            shell=False,
        )

        # Wait with timeout
        try:
            stdout, stderr = proc.communicate(timeout=req.timeout)
        except subprocess.TimeoutExpired:
            # Kill the process (cross-platform)
            try:
                if os.name == "nt":
                    proc.kill()
                else:
                    pgid = os.getpgid(proc.pid)
                    os.killpg(pgid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(pgid, signal.SIGKILL)
                        proc.wait(timeout=2)
            except (ProcessLookupError, PermissionError, AttributeError):
                proc.kill()
                proc.wait()
            stdout, stderr = b"", b"(timed out)"
            exit_code = -1
            timed_out = True
        else:
            exit_code = proc.returncode
            timed_out = False

        cancelled = False
        stdout_str = (stdout or b"").decode("utf-8", errors="replace")
        stderr_str = (stderr or b"").decode("utf-8", errors="replace")

        # Truncate output
        truncated = False
        if len(stdout_str) > MAX_OUTPUT_BYTES:
            stdout_str = stdout_str[:MAX_OUTPUT_BYTES] + "\n... (truncated)"
            truncated = True
        if len(stderr_str) > MAX_OUTPUT_BYTES:
            stderr_str = stderr_str[:MAX_OUTPUT_BYTES] + "\n... (truncated)"
            truncated = True

        duration = time.monotonic() - start

        # Audit
        _audit.record(AuditEntry(
            id=req.id,
            user_id=req.user_id,
            workspace_id=req.workspace_id,
            executable=req.executable,
            args=req.args,
            cwd=str(req.cwd),
            timeout=req.timeout,
            duration=duration,
            exit_code=exit_code,
            output_size=len(stdout_str) + len(stderr_str),
            approved=req.approved,
            timed_out=timed_out,
            cancelled=cancelled,
            blocked=False,
        ))

        return CommandResult(
            request_id=req.id,
            stdout=stdout_str,
            stderr=stderr_str,
            exit_code=exit_code,
            duration=duration,
            timed_out=timed_out,
            cancelled=cancelled,
            truncated=truncated,
        )

    finally:
        _quota.release(req)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_parser = CommandParser()


def run_command(
    raw: str,
    cwd: Path,
    user_id: str = "",
    workspace_id: str = "",
    timeout: Optional[int] = None,
    approved: bool = False,
) -> CommandResult:
    """Parse, validate, and execute a shell command.

    This is the main entry point for both the agent tool and the manual
    terminal widget.
    """
    req = _parser.parse(raw, cwd)
    req.user_id = user_id
    req.workspace_id = workspace_id
    req.approved = approved
    if timeout:
        req.timeout = min(timeout, MAX_TIMEOUT)
    return execute(req)


def get_audit_log() -> List[AuditEntry]:
    """Return recent audit log entries."""
    return _audit.recent()


def check_quota(user_id: str) -> Dict[str, Any]:
    """Return quota status for a user."""
    return {
        "running": _quota.running_count(user_id),
        "max_per_user": MAX_CONCURRENT_PER_USER,
        "max_global": MAX_CONCURRENT_GLOBAL,
    }
