"""Workspace folders management.

Persists a list of named folders and the active one in ~/.stupidex/config.json.
"""
import json
import os
import uuid
from pathlib import Path

CONFIG_FILE = Path.home() / ".stupidex" / "config.json"


def _load() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save(cfg: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def list_folders() -> list[dict]:
    cfg = _load()
    return cfg.get("folders", [])


def get_active_folder() -> dict | None:
    cfg = _load()
    folders = cfg.get("folders", [])
    active_id = cfg.get("active_folder_id")
    for f in folders:
        if f["id"] == active_id:
            return f
    return folders[0] if folders else None


def _validate_path(path_str: str) -> str:
    p = Path(path_str).expanduser()
    if not p.is_absolute():
        p = (Path.cwd() / p).resolve()
    else:
        p = p.resolve()
    if not p.exists():
        raise ValueError(f"path does not exist: {p}")
    if not p.is_dir():
        raise ValueError(f"path is not a directory: {p}")
    return str(p)


def add_folder(name: str, path_str: str) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("name is required")
    abs_path = _validate_path(path_str)

    cfg = _load()
    folders = cfg.get("folders", [])
    if any(f["path"] == abs_path for f in folders):
        raise ValueError(f"folder already attached: {abs_path}")

    folder = {
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "path": abs_path,
    }
    folders.append(folder)
    cfg["folders"] = folders
    if not cfg.get("active_folder_id"):
        cfg["active_folder_id"] = folder["id"]
    _save(cfg)
    return folder


def remove_folder(folder_id: str) -> bool:
    cfg = _load()
    folders = cfg.get("folders", [])
    new_folders = [f for f in folders if f["id"] != folder_id]
    if len(new_folders) == len(folders):
        return False
    cfg["folders"] = new_folders
    if cfg.get("active_folder_id") == folder_id:
        cfg["active_folder_id"] = new_folders[0]["id"] if new_folders else None
    _save(cfg)
    return True


def set_active_folder(folder_id: str) -> bool:
    cfg = _load()
    folders = cfg.get("folders", [])
    if not any(f["id"] == folder_id for f in folders):
        return False
    cfg["active_folder_id"] = folder_id
    _save(cfg)
    return True


def folder_summary() -> str:
    """Format the list of attached folders for inclusion in the system prompt."""
    folders = list_folders()
    if not folders:
        return "(no folders attached)"
    active_id = _load().get("active_folder_id")
    lines = []
    for f in folders:
        marker = " (active)" if f["id"] == active_id else ""
        lines.append(f"- {f['name']}: {f['path']}{marker}")
    return "\n".join(lines)
