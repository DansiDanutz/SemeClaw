"""In-memory task store used when Supabase is not configured.

Lets the /tasks page and the dialog/intervention flow run end-to-end in
the open-source demo. Persistence is file-backed JSON under
``data/v1/local_tasks.json`` so seeded tasks survive a restart.

Surface mirrors ``war_room.tasks._db`` for the subset of operations the
existing dialog + intervention code paths actually call:

  - upsert_task / get_task / list_tasks / patch_task
  - latest_dialog / list_dialogs / insert_dialog / supersede_dialog
  - list_interventions / insert_intervention

The intervention loop module only checks ``TasksDBError`` so a graceful
fallback isn't enough — we surface as a *replacement* via monkey-patch
when ``SEMECLAW_LOCAL_TASKS=1`` is set or Supabase env vars are absent.
"""

from __future__ import annotations

import asyncio
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from war_room.v1 import storage as _s


def _data_path() -> Path:
    return Path(_s._data_dir()) / "local_tasks.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fresh_state() -> dict:
    return {"tasks": {}, "dialogs": {}, "interventions": {}}


_LOCK = asyncio.Lock()


async def _read() -> dict:
    # Use a freshly-allocated default each call so multiple call sites
    # never share inner dicts (shallow-copy bug).
    return await _s.read_json(_data_path(), default=_fresh_state())


async def _write(state: dict) -> None:
    await _s.write_json(_data_path(), state)


def _gen(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


# ---------------------------------------------------------------------------
# Task CRUD
# ---------------------------------------------------------------------------
async def upsert_task(
    *,
    source: str,
    source_id: str,
    tenant_id: str,
    title: str,
    description: str | None,
    status: str,
    assigned_agents: list[str],
    meta: dict | None = None,
) -> str:
    async with _LOCK:
        state = await _read()
        # De-dup on (source, source_id, tenant_id) — same contract as _db.upsert_task.
        existing = None
        for tid, row in state["tasks"].items():
            if row.get("source") == source and row.get("source_id") == source_id and row.get("tenant_id") == tenant_id:
                existing = tid
                break
        task_id = existing or _gen("tsk")
        state["tasks"][task_id] = {
            "id": task_id,
            "source": source,
            "source_id": source_id,
            "tenant_id": tenant_id or "default",
            "title": title,
            "description": description,
            "status": status,
            "assigned_agents": list(assigned_agents or []),
            "meta": meta or {},
            "ingested_at": state["tasks"].get(task_id, {}).get("ingested_at") or _now(),
            "updated_at": _now(),
        }
        await _write(state)
        return task_id


async def list_tasks(tenant_id: str = "default", status: str | None = None, limit: int = 100) -> list[dict]:
    state = await _read()
    rows = [r for r in state["tasks"].values() if r.get("tenant_id", "default") == tenant_id]
    if status:
        rows = [r for r in rows if r.get("status") == status]
    rows.sort(key=lambda r: r.get("ingested_at") or "", reverse=True)
    return rows[: max(1, limit)]


async def get_task(task_id: str) -> dict | None:
    state = await _read()
    return state["tasks"].get(task_id)


async def patch_task(task_id: str, patch: dict) -> dict:
    async with _LOCK:
        state = await _read()
        row = state["tasks"].get(task_id)
        if not row:
            return {}
        for k, v in patch.items():
            row[k] = v
        row["updated_at"] = _now()
        state["tasks"][task_id] = row
        await _write(state)
        return row


# ---------------------------------------------------------------------------
# Dialog CRUD
# ---------------------------------------------------------------------------
async def latest_dialog(task_id: str) -> dict | None:
    state = await _read()
    rows = [d for d in state["dialogs"].values() if d.get("task_id") == task_id]
    rows.sort(key=lambda d: d.get("version", 0), reverse=True)
    return rows[0] if rows else None


async def list_dialogs(task_id: str) -> list[dict]:
    state = await _read()
    rows = [d for d in state["dialogs"].values() if d.get("task_id") == task_id]
    rows.sort(key=lambda d: d.get("version", 0))
    return rows


async def get_dialog(dialog_id: str) -> dict | None:
    state = await _read()
    return state["dialogs"].get(dialog_id)


async def insert_dialog(task_id: str, version: int, lines: list[dict]) -> dict:
    async with _LOCK:
        state = await _read()
        did = _gen("dlg")
        state["dialogs"][did] = {
            "id": did,
            "task_id": task_id,
            "version": version,
            "lines": lines,
            "created_at": _now(),
            "superseded_by": None,
        }
        await _write(state)
        return state["dialogs"][did]


async def supersede_dialog(old_dialog_id: str, new_dialog_id: str) -> None:
    async with _LOCK:
        state = await _read()
        if old_dialog_id in state["dialogs"]:
            state["dialogs"][old_dialog_id]["superseded_by"] = new_dialog_id
            await _write(state)


# ---------------------------------------------------------------------------
# Interventions
# ---------------------------------------------------------------------------
async def list_interventions(dialog_id: str) -> list[dict]:
    state = await _read()
    rows = [i for i in state["interventions"].values() if i.get("dialog_id") == dialog_id]
    rows.sort(key=lambda r: r.get("turn_index", 0))
    return rows


async def insert_intervention(
    *,
    task_id: str,
    dialog_id: str,
    turn_index: int,
    user_comment: str,
    agent_replies: list[dict],
    orchestrator_decision: dict | None = None,
) -> dict:
    async with _LOCK:
        state = await _read()
        iid = _gen("itv")
        state["interventions"][iid] = {
            "id": iid,
            "task_id": task_id,
            "dialog_id": dialog_id,
            "turn_index": turn_index,
            "user_comment": user_comment,
            "agent_replies": agent_replies,
            "orchestrator_decision": orchestrator_decision,
            "created_at": _now(),
        }
        await _write(state)
        return state["interventions"][iid]


# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------
def should_activate() -> bool:
    """Use the local store when Supabase env vars are blank OR when the
    operator explicitly forces it."""
    if os.environ.get("SEMECLAW_LOCAL_TASKS", "").lower() in {"1", "true", "yes"}:
        return True
    return not (os.environ.get("DLS_TEAM_SUPABASE_URL") and os.environ.get("DLS_TEAM_SUPABASE_SERVICE_KEY"))


def patch_into(_db_module) -> None:
    """Monkey-patch every public attribute on ``war_room.tasks._db`` to
    point at the local-store equivalents. Idempotent — safe to call twice.
    """
    if getattr(_db_module, "_v1_local_patched", False):
        return
    _db_module._v1_local_patched = True
    for name in (
        "upsert_task",
        "list_tasks",
        "get_task",
        "patch_task",
        "latest_dialog",
        "list_dialogs",
        "get_dialog",
        "insert_dialog",
        "supersede_dialog",
        "list_interventions",
        "insert_intervention",
    ):
        setattr(_db_module, name, globals()[name])


async def seed_demo_task() -> dict:
    """Insert one demo task if the store is empty, so the /tasks UI has
    something to render on a fresh install."""
    state = await _read()
    if state["tasks"]:
        for row in state["tasks"].values():
            return row
    tid = await upsert_task(
        source="local",
        source_id="demo-001",
        tenant_id="default",
        title="Investigate intermittent 500s on /api/tasks (EU)",
        description=(
            "P99 spiked at 04:12 UTC. WAF rule rolled out same window. "
            "Need root-cause + remediation. Owners: research → coder → writer."
        ),
        status="open",
        assigned_agents=["research", "coder", "writer"],
        meta={"seeded": True},
    )
    return (await _read())["tasks"][tid]
