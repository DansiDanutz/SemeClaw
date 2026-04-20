"""
War Room — Shared state helpers

Atomic JSON file I/O with file locking and corruption recovery.

The War Room writes to two files from multiple processes:
  - shared_state.json  (pipeline metrics + completed tasks)
  - task_queue.json    (pending tasks)

Concurrent writes without locking cause corruption. This module centralises
all reads/writes so every caller goes through the same atomic path.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger("war_room.state")

# Default schema for a fresh shared_state.json
DEFAULT_STATE: dict[str, Any] = {
    "version": "1.0",
    "last_updated": None,
    "active_tasks": [],
    "completed_tasks": [],
    "paperclip": {
        "connected": False,
        "last_sync": None,
        "projects": [],
        "agents": {},
    },
    "metrics": {
        "tasks_run": 0,
        "tasks_succeeded": 0,
        "tasks_failed": 0,
        "paperclip_issues_created": 0,
        "multica_issues_created": 0,
    },
}


def utc_now_iso() -> str:
    """Return ISO8601 timestamp in UTC (replaces deprecated datetime.utcnow)."""
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


# ---------------------------------------------------------------------------
# Atomic I/O
# ---------------------------------------------------------------------------

def atomic_write_json(path: Path, data: Any, *, indent: int = 2) -> None:
    """Write JSON atomically: write to a temp file in the same directory, then rename.

    Rename is atomic on POSIX and (from Python 3.3+) on Windows when the
    destination already exists.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def safe_read_json(path: Path, default: Any | None = None) -> Any:
    """Read JSON, returning `default` if the file is missing or corrupt.

    A corrupt file is renamed with a `.corrupt-<ts>` suffix so the bad data
    is preserved for debugging but does not block the pipeline.
    """
    if not path.exists():
        return default if default is not None else {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        backup = path.with_suffix(path.suffix + f".corrupt-{int(time.time())}")
        try:
            path.rename(backup)
            logger.error(
                "Corrupt JSON in %s (%s). Moved to %s. Returning default.",
                path.name, e, backup.name,
            )
        except OSError:
            logger.error("Corrupt JSON in %s (%s). Returning default.", path.name, e)
        return default if default is not None else {}


# ---------------------------------------------------------------------------
# Cross-platform file locking
# ---------------------------------------------------------------------------

@contextmanager
def file_lock(path: Path, *, timeout: float = 10.0, poll: float = 0.05) -> Iterator[None]:
    """Acquire an exclusive lock sibling to `path`.

    Uses a lockfile with atomic O_CREAT|O_EXCL. Works on both POSIX and Windows
    without extra dependencies. `timeout` raises TimeoutError on failure.
    """
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    fd = None
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            # Check age — if clearly stale, try to take over.
            try:
                age = time.time() - lock_path.stat().st_mtime
            except FileNotFoundError:
                continue
            if age > max(timeout * 2, 30):
                logger.warning(
                    "Taking over stale lock file %s (age=%.1fs)", lock_path.name, age,
                )
                try:
                    lock_path.unlink(missing_ok=True)
                    continue
                except OSError:
                    # Some filesystems refuse unlink even for our own file;
                    # fall through to O_WRONLY | O_TRUNC takeover.
                    try:
                        fd = os.open(str(lock_path), os.O_WRONLY | os.O_TRUNC)
                        break
                    except OSError:
                        pass  # retry normally
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Could not acquire lock on {path.name} within {timeout}s")
            time.sleep(poll)
    try:
        os.write(fd, str(os.getpid()).encode("utf-8"))
        os.close(fd)
        fd = None
        yield
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            lock_path.unlink(missing_ok=True)
        except OSError as e:
            # Some FUSE / networked filesystems refuse unlink even though the
            # file exists. The lockfile is best-effort — if we can't remove it
            # now, the stale-lock detection in the next acquire will handle it.
            logger.debug("Could not remove lock file %s: %s", lock_path.name, e)


# ---------------------------------------------------------------------------
# High-level state operations
# ---------------------------------------------------------------------------

def load_state(path: Path) -> dict[str, Any]:
    """Load shared state, applying defaults for any missing keys."""
    state = safe_read_json(path, default={})
    merged: dict[str, Any] = json.loads(json.dumps(DEFAULT_STATE))  # deep copy
    for key, val in (state or {}).items():
        if key in merged and isinstance(merged[key], dict) and isinstance(val, dict):
            merged[key].update(val)
        else:
            merged[key] = val
    return merged


def save_state(path: Path, state: dict[str, Any]) -> None:
    """Persist state atomically, stamping `last_updated`."""
    state["last_updated"] = utc_now_iso()
    with file_lock(path):
        atomic_write_json(path, state)


def record_completed_task(
    path: Path,
    *,
    run_id: str,
    task: str,
    agents: list[str],
    issue_id: str | None,
    multica_issue_id: str | None = None,
    succeeded: bool = True,
) -> dict[str, Any]:
    """Append a completed task entry and update metrics — locked + atomic."""
    with file_lock(path):
        state = load_state(path)
        state["completed_tasks"].append({
            "run_id":           run_id,
            "task":             task,
            "agents":           agents,
            "issue_id":         issue_id,
            "multica_issue_id": multica_issue_id,
            "succeeded":        succeeded,
            "completed_at":     utc_now_iso(),
        })
        metrics = state["metrics"]
        metrics["tasks_run"] = metrics.get("tasks_run", 0) + 1
        if succeeded:
            metrics["tasks_succeeded"] = metrics.get("tasks_succeeded", 0) + 1
        else:
            metrics["tasks_failed"] = metrics.get("tasks_failed", 0) + 1
        if issue_id:
            metrics["paperclip_issues_created"] = metrics.get("paperclip_issues_created", 0) + 1
        if multica_issue_id:
            metrics["multica_issues_created"] = metrics.get("multica_issues_created", 0) + 1
        state["last_updated"] = utc_now_iso()
        atomic_write_json(path, state)
        return state


def enqueue_task(path: Path, task: dict[str, Any]) -> None:
    """Append a task to task_queue.json atomically."""
    with file_lock(path):
        queue = safe_read_json(path, default=[])
        if not isinstance(queue, list):
            queue = []
        task.setdefault("enqueued_at", utc_now_iso())
        queue.append(task)
        atomic_write_json(path, queue)


def dequeue_task(path: Path) -> dict[str, Any] | None:
    """Pop the first task from task_queue.json atomically. Returns None if empty."""
    with file_lock(path):
        queue = safe_read_json(path, default=[])
        if not isinstance(queue, list) or not queue:
            return None
        task = queue.pop(0)
        atomic_write_json(path, queue)
        return task
