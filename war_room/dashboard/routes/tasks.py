"""Task ingest, dialog, and retention endpoints.

GET  /api/tasks                    list tasks for tenant (?status=, ?limit=)
POST /api/tasks/sync               pull from every adapter, upsert, return counts
GET  /api/tasks/{task_id}          fetch a single task
GET  /api/tasks/{task_id}/dialog   latest dialog (auto-generated on first call)
POST /api/tasks/{task_id}/dialog   regenerate (bumps version)
GET  /api/tasks/quota              {tenant_id, cap, active, archived, available, over_cap}
POST /api/tasks/gc                 enforce retention (archives oldest beyond cap)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from war_room.dashboard.routes.deps import _tenant_id
from war_room.tasks import _db, sources, dialog as _dialog, retention

logger = logging.getLogger("war_room.dashboard.tasks")
router = APIRouter(tags=["tasks"])


def _err(msg: str, code: int = 500):
    return JSONResponse({"ok": False, "error": msg}, status_code=code)


@router.get("/api/tasks")
async def api_list_tasks(request: Request,
                         status: str | None = None,
                         limit: int = 100):
    tenant = _tenant_id(request)
    try:
        rows = await _db.list_tasks(tenant_id=tenant, status=status, limit=limit)
        return {"ok": True, "tenant_id": tenant, "count": len(rows), "tasks": rows}
    except Exception as e:
        return _err(str(e))


@router.post("/api/tasks/sync")
async def api_sync_tasks(request: Request):
    tenant = _tenant_id(request)
    try:
        records = await sources.discover_all()
        synced, by_source = 0, {}
        for rec in records:
            try:
                await _db.upsert_task(
                    source=rec["source"],
                    source_id=rec["source_id"],
                    tenant_id=tenant,
                    title=rec["title"],
                    description=rec.get("description"),
                    status=rec.get("status", "open"),
                    assigned_agents=rec.get("assigned_agents", []),
                    meta=rec.get("meta", {}),
                )
                synced += 1
                by_source[rec["source"]] = by_source.get(rec["source"], 0) + 1
            except Exception as e:
                logger.warning("upsert failed for %s/%s: %s", rec.get("source"), rec.get("source_id"), e)
        # Best-effort retention pass after every sync.
        try:
            await retention.enforce(tenant)
        except Exception:
            pass
        return {"ok": True, "tenant_id": tenant, "synced": synced, "by_source": by_source}
    except Exception as e:
        return _err(str(e))


@router.get("/api/tasks/quota")
async def api_quota(request: Request):
    tenant = _tenant_id(request)
    try:
        return {"ok": True, **await retention.status(tenant)}
    except Exception as e:
        return _err(str(e))


@router.post("/api/tasks/gc")
async def api_gc(request: Request):
    tenant = _tenant_id(request)
    try:
        return {"ok": True, "result": await retention.enforce(tenant)}
    except Exception as e:
        return _err(str(e))


@router.get("/api/tasks/{task_id}")
async def api_get_task(task_id: str):
    try:
        t = await _db.get_task(task_id)
        if not t:
            return _err("task not found", 404)
        return {"ok": True, "task": t}
    except Exception as e:
        return _err(str(e))


@router.get("/api/tasks/{task_id}/dialog")
async def api_get_dialog(task_id: str):
    try:
        task = await _db.get_task(task_id)
        if not task:
            return _err("task not found", 404)
        d = await _db.latest_dialog(task_id)
        if d:
            return {"ok": True, "task_id": task_id, "dialog": d, "generated": False}
        # Auto-generate v1 on first read.
        lines = await _dialog.compose_dialog(task)
        d = await _db.insert_dialog(task_id, version=1, lines=lines)
        return {"ok": True, "task_id": task_id, "dialog": d, "generated": True}
    except Exception as e:
        return _err(str(e))


@router.post("/api/tasks/{task_id}/dialog")
async def api_regen_dialog(task_id: str):
    try:
        task = await _db.get_task(task_id)
        if not task:
            return _err("task not found", 404)
        prev = await _db.latest_dialog(task_id)
        next_ver = (prev["version"] + 1) if prev else 1
        lines = await _dialog.compose_dialog(task)
        d = await _db.insert_dialog(task_id, version=next_ver, lines=lines)
        return {"ok": True, "task_id": task_id, "dialog": d}
    except Exception as e:
        return _err(str(e))
