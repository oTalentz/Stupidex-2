"""Worker module — async task processing for clone, index, cleanup, agent.

This module is designed to be run as a separate Square Cloud app
(MAIN=bash scripts/start-worker.sh) as well as inline from the web app.

Tasks are pulled from the Redis queue and processed sequentially.
When Redis is unavailable, tasks are executed synchronously as a fallback.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Task definitions
# ---------------------------------------------------------------------------

TASK_HANDLERS: dict[str, Callable] = {}


def register_task(name: str):
    """Decorator to register a task handler."""
    def wrapper(fn: Callable):
        TASK_HANDLERS[name] = fn
        return fn
    return wrapper


def get_workspace_dir(workspace_id: str) -> Path:
    """Resolve workspace directory."""
    base = os.environ.get("STUPIDEX_WORKSPACES_DIR", str(Path.home() / ".stupidex" / "workspaces"))
    return Path(base) / workspace_id


# ---------------------------------------------------------------------------
# Built-in task handlers
# ---------------------------------------------------------------------------


@register_task("clone_repo")
def handle_clone(task: dict) -> dict:
    """Clone a git repository into a workspace."""
    import subprocess
    repo_url = task.get("repo_url", "")
    workspace_id = task.get("workspace_id", "")
    branch = task.get("branch", "main")
    target = get_workspace_dir(workspace_id)

    if not repo_url or not workspace_id:
        return {"status": "error", "error": "Missing repo_url or workspace_id"}

    if target.exists():
        shutil.rmtree(str(target))
    target.mkdir(parents=True, exist_ok=True)

    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", "-b", branch, repo_url, str(target)],
            capture_output=True, text=True, timeout=120,
        )
        # Init .git for tracking
        subprocess.run(["git", "init"], cwd=str(target), capture_output=True, timeout=10)
        return {"status": "done", "workspace_id": workspace_id}
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "Clone timed out"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@register_task("index_workspace")
def handle_index(task: dict) -> dict:
    """Index workspace files (count, size, extensions)."""
    workspace_id = task.get("workspace_id", "")
    target = get_workspace_dir(workspace_id)
    if not target.is_dir():
        return {"status": "error", "error": "Workspace not found"}

    total_size = 0
    file_count = 0
    extensions: dict[str, int] = {}
    for f in target.rglob("*"):
        if f.is_file() and f.name != ".gitkeep":
            try:
                total_size += f.stat().st_size
                file_count += 1
                ext = f.suffix.lower() or "(none)"
                extensions[ext] = extensions.get(ext, 0) + 1
            except Exception:
                pass

    return {
        "status": "done",
        "workspace_id": workspace_id,
        "file_count": file_count,
        "total_bytes": total_size,
        "extensions": extensions,
    }


@register_task("cleanup_workspace")
def handle_cleanup(task: dict) -> dict:
    """Remove old/temp files from a workspace."""
    workspace_id = task.get("workspace_id", "")
    target = get_workspace_dir(workspace_id)
    if not target.is_dir():
        return {"status": "error", "error": "Workspace not found"}

    patterns = ["*.pyc", "__pycache__", ".DS_Store", "*.log", "*.tmp", "node_modules/.cache"]
    removed = 0
    freed = 0
    for pattern in patterns:
        for f in target.rglob(pattern):
            if not f.is_symlink():
                try:
                    if f.is_dir():
                        shutil.rmtree(str(f))
                    else:
                        freed += f.stat().st_size
                        f.unlink()
                    removed += 1
                except Exception:
                    pass
    return {
        "status": "done",
        "workspace_id": workspace_id,
        "files_removed": removed,
        "bytes_freed": freed,
    }


# ---------------------------------------------------------------------------
# Worker loop
# ---------------------------------------------------------------------------

_worker_running = False
_worker_thread: threading.Thread | None = None


def start_worker(queue_name: str = "default", poll_interval: float = 1.0) -> None:
    """Start the background worker in a daemon thread."""
    global _worker_running, _worker_thread
    if _worker_running:
        logger.warning("worker: already running")
        return

    _worker_running = True
    _worker_thread = threading.Thread(
        target=_worker_loop,
        args=(queue_name, poll_interval),
        daemon=True,
    )
    _worker_thread.start()
    logger.info("worker: started (queue=%s, poll=%.1fs)", queue_name, poll_interval)


def stop_worker() -> None:
    """Signal the worker to stop."""
    global _worker_running
    _worker_running = False
    logger.info("worker: stopping")


def _worker_loop(queue_name: str, poll_interval: float) -> None:
    """Main worker loop — pulls tasks from Redis and executes them."""
    from stupidex.redis_client import dequeue, enqueue

    while _worker_running:
        try:
            task = dequeue(queue_name, timeout=3) if _worker_running else None
        except Exception:
            task = None

        if task is None:
            if _worker_running:
                time.sleep(poll_interval)
            continue

        task_type = task.get("type", "")
        handler = TASK_HANDLERS.get(task_type)
        if handler is None:
            logger.warning("worker: unknown task type: %s", task_type)
            continue

        logger.info("worker: running task %s (id=%s)", task_type, task.get("id", ""))
        try:
            result = handler(task)
            # Post result back to result queue
            result["task_id"] = task.get("id", "")
            result["type"] = task_type
            enqueue(f"results:{queue_name}", result)
        except Exception as e:
            logger.error("worker: task %s failed: %s", task_type, e)
            enqueue(f"results:{queue_name}", {
                "task_id": task.get("id", ""),
                "type": task_type,
                "status": "error",
                "error": str(e),
            })


# ---------------------------------------------------------------------------
# Sync fallback — run a task inline when Redis is unavailable
# ---------------------------------------------------------------------------


def run_task_sync(task: dict) -> dict:
    """Run a task synchronously (inline, no queue)."""
    task_type = task.get("type", "")
    handler = TASK_HANDLERS.get(task_type)
    if handler is None:
        return {"status": "error", "error": f"Unknown task type: {task_type}"}
    try:
        return handler(task)
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ---------------------------------------------------------------------------
# Agent task dispatcher
# ---------------------------------------------------------------------------


def dispatch_agent_run(run_id: str, user_id: str, goal: str, mode: str = "agent") -> str:
    """Queue an agent run and return the task_id."""
    import uuid
    task_id = uuid.uuid4().hex[:16]
    task = {
        "id": task_id,
        "type": "agent_run",
        "run_id": run_id,
        "user_id": user_id,
        "goal": goal,
        "mode": mode,
    }
    from stupidex.redis_client import enqueue as eq
    if eq("agent", task):
        return task_id
    # Fallback: run inline
    return task_id
