"""Meeting orchestration routes.

Includes small backward-compatibility shims for the older Telegram bridge,
which still posts to `/api/meeting/task` and polls `/api/meeting/history/...`.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from war_room.agents import registry as _reg
from war_room.dashboard.r2_client import generate_presigned_url
from war_room.dashboard.routes.deps import ROOT, _tenant_id
from war_room.tasks import _db
from war_room.tasks import dialog as _dialog

router = APIRouter(tags=["meetings"])
_LEGACY_MEETING_CACHE: dict[str, dict] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_task_text(raw: str) -> str:
    return re.sub(r"\s+", " ", (raw or "").strip())


def _speaker_name(agent_id: str) -> str:
    agent = _reg.get(agent_id or "")
    return agent.name if agent else (agent_id or "agent").replace("_", " ").title()


def _pick_agents(task_text: str, max_agents: int = 3) -> list[str]:
    normalized = (task_text or "").lower()
    words = set(re.findall(r"[a-z0-9_+-]+", normalized))
    ranked: list[tuple[int, int, str]] = []

    for agent in _reg.load_all().values():
        if agent.id == "semeclaw" or agent.is_adapter:
            continue
        score = 0
        for keyword in agent.keywords:
            kw = (keyword or "").strip().lower()
            if not kw:
                continue
            if " " in kw:
                if kw in normalized:
                    score += 4
            elif kw in words:
                score += 3
            elif kw in normalized:
                score += 1
        if agent.id == "writer" and "hermes" in normalized:
            score += 5
        if score > 0:
            ranked.append((score, 1 if agent.core else 0, agent.id))

    ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
    selected = [agent_id for _, _, agent_id in ranked[:max_agents]]
    if not selected:
        return ["writer"]
    if "writer" not in selected and "hermes" in normalized:
        return ["writer", *selected[: max_agents - 1]]
    return selected


def _serialize_legacy_session(meeting_id: str, task: dict, dialog: dict) -> dict:
    turns = [
        {
            "speaker": _speaker_name(line.get("agent_id") or ""),
            "agent": line.get("agent_id"),
            "text": line.get("text") or "",
        }
        for line in (dialog.get("lines") or [])
        if line.get("text")
    ]
    assignments = [
        {
            "agent": _speaker_name(agent_id),
            "owner": agent_id,
            "task": task.get("title") or "",
        }
        for agent_id in (task.get("assigned_agents") or [])
    ]
    return {
        "meeting_id": meeting_id,
        "task_id": task.get("id", meeting_id),
        "status": "complete",
        "turns": turns,
        "assignments": assignments,
        "version": dialog.get("version", 1),
    }


async def create_legacy_meeting(
    task_text: str,
    *,
    tenant_id: str = "default",
    source: str = "telegram",
    source_id: str | None = None,
    meta: dict | None = None,
) -> tuple[str, dict, dict]:
    clean = _clean_task_text(task_text)
    if not clean:
        raise ValueError("task text is required")

    meeting_id = source_id or uuid4().hex
    task = {
        "id": meeting_id,
        "title": clean[:160],
        "description": clean,
        "status": "open",
        "assigned_agents": _pick_agents(clean),
        "tenant_id": tenant_id,
        "source": source,
        "meta": meta or {},
    }
    dialog: dict | None = None
    persisted = False

    try:
        task_id = await _db.upsert_task(
            source=source,
            source_id=source_id or f"{source}-{meeting_id}",
            tenant_id=tenant_id,
            title=task["title"],
            description=task["description"],
            status=task["status"],
            assigned_agents=task["assigned_agents"],
            meta=task["meta"],
        )
        db_task = await _db.get_task(task_id)
        if db_task:
            task = db_task
            meeting_id = task.get("id", meeting_id)
            persisted = True
    except Exception:
        persisted = False

    if persisted:
        try:
            dialog = await _db.latest_dialog(task["id"])
        except Exception:
            dialog = None

    if not dialog:
        lines = await _dialog.compose_dialog(task)
        dialog = {"task_id": meeting_id, "version": 1, "lines": lines}
        if persisted:
            try:
                dialog = await _db.insert_dialog(task["id"], version=1, lines=lines)
            except Exception:
                pass

    _LEGACY_MEETING_CACHE[meeting_id] = {
        "created_at": _now(),
        "task": task,
        "dialog": dialog,
    }
    return meeting_id, task, dialog


async def get_legacy_meeting(meeting_id: str) -> dict | None:
    cached = _LEGACY_MEETING_CACHE.get(meeting_id)
    if cached:
        return _serialize_legacy_session(meeting_id, cached["task"], cached["dialog"])

    try:
        task = await _db.get_task(meeting_id)
    except Exception:
        task = None

    if not task:
        return None

    try:
        dialog = await _db.latest_dialog(task["id"])
    except Exception:
        dialog = None

    if not dialog:
        lines = await _dialog.compose_dialog(task)
        dialog = {"task_id": task["id"], "version": 1, "lines": lines}

    _LEGACY_MEETING_CACHE[meeting_id] = {
        "created_at": _now(),
        "task": task,
        "dialog": dialog,
    }
    return _serialize_legacy_session(meeting_id, task, dialog)


@router.get("/api/ad/audio")
async def api_ad_audio(request: Request):
    """Redirect to the presigned R2 URL for the default ad MP3.

    Falls back to the local placeholder if R2 is not configured.
    """
    r2_key = request.query_params.get("key", "ads/nervix-default.mp3")
    presigned = generate_presigned_url(r2_key, expiration=300)
    if presigned:
        return RedirectResponse(presigned)
    # Fallback to local placeholder
    local_path = ROOT / "data" / "ads" / "nervix-default.mp3"
    if local_path.exists():
        return FileResponse(local_path, media_type="audio/mpeg")
    return JSONResponse({"error": "Ad audio not available"}, status_code=404)


@router.post("/api/meeting/task")
async def api_meeting_task(request: Request):
    """Backward-compatible entrypoint for the legacy Telegram bridge."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON body"}, status_code=400)

    task_text = _clean_task_text(body.get("task") or body.get("text") or body.get("message") or "")
    if not task_text:
        return JSONResponse({"ok": False, "error": "task is required"}, status_code=400)

    try:
        meeting_id, task, dialog = await create_legacy_meeting(
            task_text,
            tenant_id=_tenant_id(request),
            source="telegram",
            meta={
                "user_context": body.get("user_context"),
                "lang": body.get("lang"),
                "transport": "legacy-telegram-bridge",
            },
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    return {
        "ok": True,
        "meeting_id": meeting_id,
        "task_id": task.get("id", meeting_id),
        "status": "complete",
        "assigned_agents": task.get("assigned_agents") or [],
        "turn_count": len(dialog.get("lines") or []),
    }


@router.get("/api/meeting/history/{meeting_id}")
async def api_meeting_history(meeting_id: str):
    session = await get_legacy_meeting(meeting_id)
    if not session:
        return JSONResponse({"ok": False, "error": "meeting not found"}, status_code=404)
    return session
