"""Bidirectional write-back: push the orchestrator's patched task to its source.

Each source has a different shape — we map our canonical fields to the source's
update endpoint. If creds are missing or the source doesn't expose a write API,
we return a clean `{ok: false, reason}` instead of raising.

Sources that round-trip:
  paperclip   PATCH {base}/api/tasks/{source_id}
  moltica     PATCH {base}/v1/tasks/{source_id}
  local       writes the patched JSON back to war_room/tasks/inbox/<id>.json
  claude_code (read-only — Claude Code tasks live in user files we don't touch)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx


def _payload(task: dict) -> dict:
    """Map our canonical task to a generic update payload most APIs accept."""
    return {
        "title":       task.get("title"),
        "description": task.get("description"),
        "status":      task.get("status"),
        "agents":      task.get("assigned_agents") or [],
    }


async def _http_patch(url: str, body: dict, headers: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=12.0) as c:
            r = await c.patch(url, json=body, headers=headers)
            return {"ok": r.status_code < 400, "status": r.status_code,
                    "body": (r.text or "")[:240]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def _push_paperclip(task: dict) -> dict:
    base = os.environ.get("PAPERCLIP_BASE_URL", "").strip()
    key  = os.environ.get("PAPERCLIP_API_KEY", "").strip()
    if not (base and key):
        return {"ok": False, "skipped": "PAPERCLIP_BASE_URL / _API_KEY unset"}
    sid = task.get("source_id")
    if not sid:
        return {"ok": False, "skipped": "task has no source_id"}
    return await _http_patch(
        f"{base.rstrip('/')}/api/tasks/{sid}",
        _payload(task),
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )


async def _push_moltica(task: dict) -> dict:
    base = os.environ.get("MOLTICA_BASE_URL", "").strip()
    key  = os.environ.get("MOLTICA_API_KEY", "").strip()
    if not (base and key):
        return {"ok": False, "skipped": "MOLTICA_BASE_URL / _API_KEY unset"}
    sid = task.get("source_id")
    if not sid:
        return {"ok": False, "skipped": "task has no source_id"}
    return await _http_patch(
        f"{base.rstrip('/')}/v1/tasks/{sid}",
        _payload(task),
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )


async def _push_local(task: dict) -> dict:
    sid = task.get("source_id")
    if not sid:
        return {"ok": False, "skipped": "task has no source_id"}
    inbox = Path(__file__).parent / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    f = inbox / f"{sid}.json"
    payload = {
        "id":              sid,
        "title":           task.get("title"),
        "description":     task.get("description"),
        "status":          task.get("status"),
        "assigned_agents": task.get("assigned_agents") or [],
    }
    f.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {"ok": True, "wrote": str(f)}


async def _push_claude_code(task: dict) -> dict:
    return {"ok": False, "skipped": "claude_code is read-only by design"}


_DISPATCH = {
    "paperclip":    _push_paperclip,
    "moltica":      _push_moltica,
    "local":        _push_local,
    "claude_code":  _push_claude_code,
}


async def push_to_source(task: dict) -> dict:
    """Write back the patched task to wherever it came from."""
    src = task.get("source")
    fn = _DISPATCH.get(src or "")
    if not fn:
        return {"ok": False, "skipped": f"no writeback handler for source '{src}'"}
    return await fn(task)
