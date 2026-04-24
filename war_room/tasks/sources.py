"""Task source plugins.

Each source produces a stream of dict records with a stable shape:
    {
      "source":          "paperclip" | "moltica" | "local" | "claude_code",
      "source_id":       str,
      "title":           str,
      "description":     str | None,
      "status":          "open" | "in_progress" | "needs_review" | "done" | "archived",
      "assigned_agents": list[str],   # registry agent ids
      "meta":            dict,        # raw payload from source
    }

Adapter env vars (see war_room/agents/adapters/*.md):
    PAPERCLIP_BASE_URL / PAPERCLIP_API_KEY
    MOLTICA_BASE_URL   / MOLTICA_API_KEY

If an adapter's env is missing, the source is silently skipped — the demo
must still work with zero adapters configured (local files only).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import AsyncIterator

import httpx


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _norm_agents(raw) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        return [a.strip() for a in raw.split(",") if a.strip()]
    if isinstance(raw, list):
        return [str(a).strip() for a in raw if str(a).strip()]
    return []


def _norm_status(raw: str | None) -> str:
    s = (raw or "open").lower()
    if s in {"open", "in_progress", "needs_review", "done", "archived"}:
        return s
    if s in {"todo", "new", "backlog"}:           return "open"
    if s in {"doing", "wip", "active"}:           return "in_progress"
    if s in {"review", "qa", "blocked"}:          return "needs_review"
    if s in {"closed", "resolved", "complete"}:   return "done"
    return "open"


# ---------------------------------------------------------------------------
# Paperclip adapter
# ---------------------------------------------------------------------------
async def paperclip_tasks() -> AsyncIterator[dict]:
    base = os.environ.get("PAPERCLIP_BASE_URL", "").strip()
    key  = os.environ.get("PAPERCLIP_API_KEY", "").strip()
    if not base or not key:
        return
    url = base.rstrip("/") + "/api/tasks"
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(url, headers={"Authorization": f"Bearer {key}"})
            r.raise_for_status()
            data = r.json()
    except Exception:
        return
    items = data.get("tasks") or data.get("items") or data if isinstance(data, list) else []
    for it in (items or []):
        yield {
            "source":          "paperclip",
            "source_id":       str(it.get("id") or it.get("uuid") or ""),
            "title":           it.get("title") or it.get("name") or "(untitled)",
            "description":     it.get("description") or it.get("body"),
            "status":          _norm_status(it.get("status")),
            "assigned_agents": _norm_agents(it.get("agents") or it.get("assigned_agents")),
            "meta":            it,
        }


# ---------------------------------------------------------------------------
# Moltica adapter
# ---------------------------------------------------------------------------
async def moltica_tasks() -> AsyncIterator[dict]:
    base = os.environ.get("MOLTICA_BASE_URL", "").strip()
    key  = os.environ.get("MOLTICA_API_KEY", "").strip()
    if not base or not key:
        return
    url = base.rstrip("/") + "/v1/tasks"
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(url, headers={"Authorization": f"Bearer {key}"})
            r.raise_for_status()
            data = r.json()
    except Exception:
        return
    for it in (data.get("data") or data.get("tasks") or []):
        yield {
            "source":          "moltica",
            "source_id":       str(it.get("id") or ""),
            "title":           it.get("title") or it.get("subject") or "(untitled)",
            "description":     it.get("description"),
            "status":          _norm_status(it.get("state") or it.get("status")),
            "assigned_agents": _norm_agents(it.get("assignees") or it.get("agents")),
            "meta":            it,
        }


# ---------------------------------------------------------------------------
# Local-file adapter — drop *.json files into war_room/tasks/inbox/
# ---------------------------------------------------------------------------
def _inbox_dir() -> Path:
    p = Path(__file__).parent / "inbox"
    p.mkdir(parents=True, exist_ok=True)
    return p


async def local_tasks() -> AsyncIterator[dict]:
    for f in sorted(_inbox_dir().glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        yield {
            "source":          "local",
            "source_id":       data.get("id") or f.stem,
            "title":           data.get("title") or f.stem,
            "description":     data.get("description"),
            "status":          _norm_status(data.get("status")),
            "assigned_agents": _norm_agents(data.get("assigned_agents") or data.get("agents")),
            "meta":            data,
        }


# ---------------------------------------------------------------------------
# Claude Code adapter — reads ~/.claude/projects/*/<file>.jsonl recent tasks
# (best-effort; silently skipped if directory missing)
# ---------------------------------------------------------------------------
async def claude_code_tasks() -> AsyncIterator[dict]:
    root = Path.home() / ".claude" / "projects"
    if not root.exists():
        return
    # We don't deep-scan — only project-level "tasks.json" if present.
    for proj in sorted(root.iterdir()):
        tf = proj / "tasks.json"
        if not tf.exists():
            continue
        try:
            data = json.loads(tf.read_text(encoding="utf-8"))
        except Exception:
            continue
        for it in data if isinstance(data, list) else data.get("tasks", []):
            yield {
                "source":          "claude_code",
                "source_id":       str(it.get("id") or ""),
                "title":           it.get("title") or "(untitled)",
                "description":     it.get("description"),
                "status":          _norm_status(it.get("status")),
                "assigned_agents": _norm_agents(it.get("agents")),
                "meta":            {**it, "project": proj.name},
            }


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------
SOURCES = {
    "paperclip":    paperclip_tasks,
    "moltica":      moltica_tasks,
    "local":        local_tasks,
    "claude_code":  claude_code_tasks,
}


async def discover_all() -> list[dict]:
    """Pull from every configured source. Sources with missing env are skipped."""
    out: list[dict] = []
    for name, fn in SOURCES.items():
        try:
            async for rec in fn():
                if rec.get("source_id"):
                    out.append(rec)
        except Exception:
            continue
    return out
