"""Tests for war_room.state — atomic I/O, locking, metrics bookkeeping."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from state import (
    DEFAULT_STATE,
    atomic_write_json,
    dequeue_task,
    enqueue_task,
    file_lock,
    load_state,
    record_completed_task,
    safe_read_json,
    save_state,
)


def test_atomic_write_json_creates_file(tmp_path: Path) -> None:
    path = tmp_path / "out.json"
    atomic_write_json(path, {"a": 1})
    assert json.loads(path.read_text()) == {"a": 1}


def test_atomic_write_json_replaces_existing(tmp_path: Path) -> None:
    path = tmp_path / "out.json"
    path.write_text(json.dumps({"old": True}))
    atomic_write_json(path, {"new": True})
    assert json.loads(path.read_text()) == {"new": True}
    # No leftover temp files
    leftovers = [p for p in tmp_path.iterdir() if p.name != "out.json"]
    assert leftovers == []


def test_safe_read_json_missing_returns_default(tmp_path: Path) -> None:
    assert safe_read_json(tmp_path / "missing.json", default={"ok": True}) == {"ok": True}


def test_safe_read_json_corrupt_backs_up(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json")
    result = safe_read_json(path, default={"fallback": True})
    assert result == {"fallback": True}
    assert not path.exists()  # renamed
    backups = list(tmp_path.glob("bad.json.corrupt-*"))
    assert len(backups) == 1


def test_load_state_applies_defaults(tmp_path: Path) -> None:
    path = tmp_path / "shared_state.json"
    path.write_text(json.dumps({"metrics": {"tasks_run": 5}}))
    state = load_state(path)
    assert state["metrics"]["tasks_run"] == 5
    # Defaults that weren't in the file
    assert state["completed_tasks"] == []
    assert state["paperclip"]["connected"] is False
    assert state["version"] == DEFAULT_STATE["version"]


def test_save_state_stamps_timestamp(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    save_state(path, {"metrics": {}})
    data = json.loads(path.read_text())
    assert data["last_updated"]  # ISO string


def test_record_completed_task_increments_metrics(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    record_completed_task(
        path,
        run_id="abc",
        task="Demo",
        agents=["research"],
        issue_id="PC-1",
        succeeded=True,
    )
    record_completed_task(
        path,
        run_id="def",
        task="Demo2",
        agents=["research"],
        issue_id=None,
        succeeded=False,
    )
    state = load_state(path)
    assert state["metrics"]["tasks_run"] == 2
    assert state["metrics"]["tasks_succeeded"] == 1
    assert state["metrics"]["tasks_failed"] == 1
    assert state["metrics"]["paperclip_issues_created"] == 1
    assert len(state["completed_tasks"]) == 2


def test_enqueue_and_dequeue_task(tmp_path: Path) -> None:
    queue = tmp_path / "queue.json"
    enqueue_task(queue, {"task": "A"})
    enqueue_task(queue, {"task": "B"})
    first = dequeue_task(queue)
    second = dequeue_task(queue)
    third = dequeue_task(queue)
    assert first["task"] == "A"
    assert "enqueued_at" in first
    assert second["task"] == "B"
    assert third is None


def test_file_lock_is_exclusive(tmp_path: Path) -> None:
    path = tmp_path / "thing.json"
    holding = threading.Event()
    released = threading.Event()

    def hold():
        with file_lock(path, timeout=1.0):
            holding.set()
            released.wait(1.0)

    t = threading.Thread(target=hold)
    t.start()
    assert holding.wait(1.0)
    with pytest.raises(TimeoutError):
        with file_lock(path, timeout=0.2):
            pass
    released.set()
    t.join()


def test_concurrent_record_completed_task_no_loss(tmp_path: Path) -> None:
    """Locking must keep metrics correct across many threads."""
    path = tmp_path / "state.json"

    def bump(i: int) -> None:
        record_completed_task(
            path,
            run_id=str(i),
            task=f"T{i}",
            agents=["research"],
            issue_id=f"PC-{i}",
            succeeded=True,
        )

    threads = [threading.Thread(target=bump, args=(i,)) for i in range(15)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    state = load_state(path)
    assert state["metrics"]["tasks_run"] == 15
    assert state["metrics"]["tasks_succeeded"] == 15
    assert state["metrics"]["paperclip_issues_created"] == 15
    assert len(state["completed_tasks"]) == 15
