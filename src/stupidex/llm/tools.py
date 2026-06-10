import os
import re
import shutil
import subprocess
from pathlib import Path

MAX_FILE_BYTES = 256 * 1024


def _resolve(path: str, base: Path) -> Path:
    p = Path(path) if path else base
    if not p.is_absolute():
        p = (base / p).resolve()
    return p


def read_file(path: str, working_dir: str = ".") -> str:
    base = Path(working_dir).resolve() if working_dir else Path.cwd()
    p = _resolve(path, base)
    if not p.exists():
        return f"ERROR: file not found: {p}"
    if p.is_dir():
        return f"ERROR: path is a directory: {p}"
    size = p.stat().st_size
    if size > MAX_FILE_BYTES:
        return f"ERROR: file too large ({size} bytes, max {MAX_FILE_BYTES})"
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"ERROR: file is not valid UTF-8 (binary?): {p}"


def write_file(path: str, content: str, working_dir: str = ".") -> str:
    base = Path(working_dir).resolve() if working_dir else Path.cwd()
    p = _resolve(path, base)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"OK: wrote {len(content)} bytes to {p}"


def edit_file(
    path: str,
    old_text: str,
    new_text: str,
    replace_all: bool = False,
    working_dir: str = ".",
) -> str:
    base = Path(working_dir).resolve() if working_dir else Path.cwd()
    p = _resolve(path, base)
    if not p.exists():
        return f"ERROR: file not found: {p}"
    original = p.read_text(encoding="utf-8")
    count = original.count(old_text)
    if count == 0:
        return f"ERROR: old_text not found in {p}"
    if count > 1 and not replace_all:
        return f"ERROR: old_text matches {count} locations in {p}; pass replace_all=true or use a more specific snippet"
    updated = original.replace(old_text, new_text) if replace_all else original.replace(old_text, new_text, 1)
    p.write_text(updated, encoding="utf-8")
    return f"OK: edited {p} (replaced {count if replace_all else 1} occurrence(s))"


def list_dir(path: str = ".", working_dir: str = ".") -> str:
    base = Path(working_dir).resolve() if working_dir else Path.cwd()
    p = _resolve(path, base)
    if not p.exists():
        return f"ERROR: directory not found: {p}"
    if not p.is_dir():
        return f"ERROR: not a directory: {p}"
    entries = []
    for entry in sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
        if any(part.startswith(".git") for part in entry.parts) and entry.name == ".git":
            continue
        suffix = "/" if entry.is_dir() else ""
        try:
            size = "" if entry.is_dir() else f"  {entry.stat().st_size:>8} B"
        except OSError:
            size = "  ?"
        entries.append(f"{entry.name}{suffix}{size}")
    return "\n".join(entries) or "(empty)"


def search_files(path: str, pattern: str, recursive: bool = True, working_dir: str = ".") -> str:
    base = Path(working_dir).resolve() if working_dir else Path.cwd()
    p = _resolve(path, base)
    if not p.exists():
        return f"ERROR: path not found: {p}"
    rx = re.compile(pattern)
    matches: list[str] = []
    if p.is_file():
        try:
            content = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return f"ERROR: cannot read {p}"
        for i, line in enumerate(content.splitlines(), 1):
            if rx.search(line):
                matches.append(f"{p}:{i}:{line.rstrip()}")
        return "\n".join(matches) or "(no matches)"
    candidates = [p] if not recursive else [e for e in p.rglob("*") if e.is_file()]
    for f in candidates:
        if any(part.startswith(".git") or part == "__pycache__" or part == "node_modules" for part in f.parts):
            continue
        try:
            content = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(content.splitlines(), 1):
            if rx.search(line):
                matches.append(f"{f}:{i}:{line.rstrip()}")
    return "\n".join(matches[:500]) or "(no matches)"


def mkdir(path: str, working_dir: str = ".") -> str:
    base = Path(working_dir).resolve() if working_dir else Path.cwd()
    p = _resolve(path, base)
    p.mkdir(parents=True, exist_ok=True)
    return f"OK: created directory {p}"


def delete(path: str, working_dir: str = ".") -> str:
    base = Path(working_dir).resolve() if working_dir else Path.cwd()
    p = _resolve(path, base)
    if not p.exists():
        return f"ERROR: path not found: {p}"
    if p.is_dir():
        shutil.rmtree(p)
        return f"OK: removed directory {p}"
    p.unlink()
    return f"OK: removed file {p}"


def run_shell(command: str, cwd: str | None = None, timeout: int = 60) -> str:
    work = Path(cwd).resolve() if cwd else Path.cwd()
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(work),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: command timed out after {timeout}s"
    parts = []
    if result.stdout.strip():
        parts.append(f"stdout:\n{result.stdout.rstrip()}")
    if result.stderr.strip():
        parts.append(f"stderr:\n{result.stderr.rstrip()}")
    parts.append(f"[exit {result.returncode}]")
    return "\n".join(parts) or f"[exit {result.returncode}] (no output)"


def git(args: str, cwd: str | None = None) -> str:
    work = Path(cwd).resolve() if cwd else Path.cwd()
    return run_shell(f"git {args}", cwd=str(work), timeout=120)


def _wd_arg(working_dir: str | None) -> str:
    return working_dir if working_dir else "."


TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file. Returns the file text or an error. Path is relative to working_dir unless absolute.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "working_dir": {"type": "string", "description": "Base directory (default: current)."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Overwrite a file with the given content. Creates parent directories as needed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "working_dir": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace a specific snippet of text inside an existing file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                    "replace_all": {"type": "boolean", "default": False},
                    "working_dir": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List entries of a directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": "."},
                    "working_dir": {"type": "string"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search for a regex pattern inside files (like ripgrep). Returns matching lines with file:line:content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "pattern": {"type": "string"},
                    "recursive": {"type": "boolean", "default": True},
                    "working_dir": {"type": "string"},
                },
                "required": ["path", "pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mkdir",
            "description": "Create a directory (with parents).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "working_dir": {"type": "string"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete",
            "description": "Delete a file or directory (recursively).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "working_dir": {"type": "string"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Execute a shell command. Returns stdout, stderr and exit code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "cwd": {"type": "string"},
                    "timeout": {"type": "integer", "default": 60},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git",
            "description": "Run a git command.",
            "parameters": {
                "type": "object",
                "properties": {
                    "args": {"type": "string"},
                    "cwd": {"type": "string"},
                },
                "required": ["args"],
            },
        },
    },
]


def _dispatch(name: str, args: dict) -> str:
    wd = args.get("working_dir")
    if name == "read_file":
        return read_file(args["path"], wd or ".")
    if name == "write_file":
        return write_file(args["path"], args["content"], wd or ".")
    if name == "edit_file":
        return edit_file(
            args["path"], args["old_text"], args["new_text"], args.get("replace_all", False), wd or "."
        )
    if name == "list_dir":
        return list_dir(args.get("path", "."), wd or ".")
    if name == "search_files":
        return search_files(args["path"], args["pattern"], args.get("recursive", True), wd or ".")
    if name == "mkdir":
        return mkdir(args["path"], wd or ".")
    if name == "delete":
        return delete(args["path"], wd or ".")
    if name == "run_shell":
        return run_shell(args["command"], args.get("cwd"), args.get("timeout", 60))
    if name == "git":
        return git(args["args"], args.get("cwd"))
    return f"ERROR: unknown tool '{name}'"


TOOL_FUNCTIONS = {
    "read_file": lambda a: _dispatch("read_file", a),
    "write_file": lambda a: _dispatch("write_file", a),
    "edit_file": lambda a: _dispatch("edit_file", a),
    "list_dir": lambda a: _dispatch("list_dir", a),
    "search_files": lambda a: _dispatch("search_files", a),
    "mkdir": lambda a: _dispatch("mkdir", a),
    "delete": lambda a: _dispatch("delete", a),
    "run_shell": lambda a: run_shell(a["command"], a.get("cwd"), a.get("timeout", 60)),
    "git": lambda a: git(a["args"], a.get("cwd")),
}
