"""Supabase REST helper for the task subsystem.

Reuses the shared SUPA_URL / SUPA_KEY / SUPA_HEADERS from
war_room.dashboard.routes.deps so we stay consistent with the rest of the app.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx

from war_room.dashboard.routes.deps import SUPA_HEADERS, SUPA_KEY, SUPA_URL
from war_room.utils.supa_durable import dlq_append, with_retry


class TasksDBError(RuntimeError):
    pass


_TASKS_DLQ_PATH = Path(os.environ.get("SEMECLAW_TASKS_DLQ_PATH", "/app/data/semeclaw_tasks_dlq.jsonl")).expanduser()


async def _supa_once(method: str, path: str, **kwargs):
    """Single-attempt Supabase REST call. Raises TasksDBError on any failure."""
    if not SUPA_URL or not SUPA_KEY:
        raise TasksDBError("Supabase not configured (DLS_TEAM_SUPABASE_URL / _SERVICE_KEY)")
    async with httpx.AsyncClient(base_url=SUPA_URL, timeout=15.0) as c:
        r = await getattr(c, method)(f"/rest/v1/{path}", headers=SUPA_HEADERS, **kwargs)
        if r.status_code >= 400:
            raise TasksDBError(f"{method.upper()} {path} -> {r.status_code} {r.text[:240]}")
        return r.json() if r.content else []


async def supa(method: str, path: str, **kwargs):
    """Retrying Supabase REST call.

    Transient failures (network blips, 5xx) are retried with exponential
    backoff via ``war_room.utils.supa_durable.with_retry``. Non-write calls
    (GET) that exhaust retries re-raise so callers decide. Write calls
    (POST/PATCH/DELETE) additionally persist the failed payload to the
    task DLQ so an operator / cron can replay them when Supabase recovers.
    """
    try:
        return await with_retry(_supa_once, method, path, **kwargs)
    except Exception as exc:  # noqa: BLE001
        if method.lower() in ("post", "patch", "put", "delete"):
            dlq_append(
                _TASKS_DLQ_PATH,
                {
                    "kind": f"tasks_{method.lower()}",
                    "path": path,
                    "payload": kwargs.get("json"),
                    "error": str(exc),
                },
            )
        raise


# ---- task CRUD --------------------------------------------------------------
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
    rows = await supa(
        "post",
        "rpc/semeclaw_upsert_task",
        json={
            "p_source": source,
            "p_source_id": source_id,
            "p_tenant_id": tenant_id or "default",
            "p_title": title,
            "p_description": description,
            "p_status": status,
            "p_assigned_agents": assigned_agents,
            "p_meta": meta or {},
        },
    )
    # RPC returns the uuid scalar
    if isinstance(rows, list) and rows:
        return rows[0] if isinstance(rows[0], str) else rows[0].get("semeclaw_upsert_task")
    return rows  # type: ignore[return-value]


async def list_tasks(tenant_id: str = "default", status: str | None = None, limit: int = 100) -> list[dict]:
    q = f"semeclaw_tasks?tenant_id=eq.{tenant_id}&select=*&order=ingested_at.desc&limit={limit}"
    if status:
        q += f"&status=eq.{status}"
    return await supa("get", q)


async def get_task(task_id: str) -> dict | None:
    rows = await supa("get", f"semeclaw_tasks?id=eq.{task_id}&select=*&limit=1")
    return rows[0] if rows else None


# ---- dialog CRUD ------------------------------------------------------------
async def latest_dialog(task_id: str) -> dict | None:
    rows = await supa("get", f"semeclaw_dialogs?task_id=eq.{task_id}&order=version.desc&limit=1&select=*")
    return rows[0] if rows else None


async def list_dialogs(task_id: str) -> list[dict]:
    """All dialog versions for a task, oldest first. Used for history nav in the UI."""
    return await supa("get", f"semeclaw_dialogs?task_id=eq.{task_id}&order=version.asc&select=*")


async def get_dialog(dialog_id: str) -> dict | None:
    rows = await supa("get", f"semeclaw_dialogs?id=eq.{dialog_id}&select=*&limit=1")
    return rows[0] if rows else None


async def insert_dialog(task_id: str, version: int, lines: list[dict]) -> dict:
    rows = await supa(
        "post",
        "semeclaw_dialogs",
        json={
            "task_id": task_id,
            "version": version,
            "lines": lines,
        },
    )
    return rows[0] if rows else {}


# ---- interventions ----------------------------------------------------------
async def list_interventions(dialog_id: str) -> list[dict]:
    return await supa("get", f"semeclaw_interventions?dialog_id=eq.{dialog_id}&order=turn_index.asc&select=*")


async def insert_intervention(
    *,
    task_id: str,
    dialog_id: str,
    turn_index: int,
    user_comment: str,
    agent_replies: list[dict],
    orchestrator_decision: dict | None = None,
) -> dict:
    rows = await supa(
        "post",
        "semeclaw_interventions",
        json={
            "task_id": task_id,
            "dialog_id": dialog_id,
            "turn_index": turn_index,
            "user_comment": user_comment,
            "agent_replies": agent_replies,
            "orchestrator_decision": orchestrator_decision,
        },
    )
    return rows[0] if rows else {}


async def supersede_dialog(old_dialog_id: str, new_dialog_id: str) -> None:
    await supa("patch", f"semeclaw_dialogs?id=eq.{old_dialog_id}", json={"superseded_by": new_dialog_id})


async def patch_task(task_id: str, patch: dict) -> dict:
    rows = await supa("patch", f"semeclaw_tasks?id=eq.{task_id}", json=patch)
    return rows[0] if rows else {}


# ---- retention --------------------------------------------------------------
async def quota(tenant_id: str = "default") -> dict:
    return await supa("post", "rpc/semeclaw_quota", json={"p_tenant_id": tenant_id})


async def enforce_retention(tenant_id: str = "default", cap: int = 100) -> dict:
    return await supa("post", "rpc/semeclaw_enforce_retention", json={"p_tenant_id": tenant_id, "p_cap": cap})
