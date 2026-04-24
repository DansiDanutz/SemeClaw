"""Generate a meeting-room dialog from a task + its assigned agents.

Per agent .md spec (war_room/agents/*.md):
  - One line per assigned agent per turn, in role-appropriate voice
  - Open with a 1-line scene-setter from SemeClaw (the orchestrator)
  - Close with a 1-line summary from SemeClaw proposing next action
  - Total 6-12 lines (4 if trivial)

Phase A keeps it deterministic + offline-friendly:
  - If OPENROUTER_API_KEY is set we call a small free model per agent
  - Otherwise we fall back to a templated voice keyed off the agent's role
This means the live demo + open-source clone work with zero API keys, and
upgrade gracefully when keys are added.
"""
from __future__ import annotations

import os
import urllib.parse
from datetime import datetime, timezone
from typing import Iterable

from war_room.agents import registry as _reg


def _audio_url(text: str, speaker: str) -> str:
    """Build a /api/tts URL the frontend can drop straight into <audio src=...>."""
    qs = urllib.parse.urlencode({"text": text or "", "speaker": speaker, "lang": "en"})
    return f"/api/tts?{qs}"


# ---------------------------------------------------------------------------
# Templated voice fallback (no API needed)
# ---------------------------------------------------------------------------
_VOICE_TEMPLATES = {
    "research":  "I'll dig up sources on '{title}' and surface the 3 most-cited references.",
    "writer":    "I can draft '{title}' once research lands — leaning on a brief, scannable structure.",
    "scraping":  "I'll pull the raw content for '{title}' from the listed URLs and clean it up.",
    "browser":   "I'll run the search queries needed to ground '{title}' in current results.",
    "coder":     "If this needs code I'll scaffold the smallest working version of '{title}' first.",
    "architect": "From an architecture lens, '{title}' should stay layered and avoid premature coupling.",
    "strategist": "Strategically, '{title}' lands inside our current quarter priorities — let's scope tight.",
}


def _template_line(agent_id: str, role: str, title: str) -> str:
    tpl = _VOICE_TEMPLATES.get(agent_id)
    if tpl:
        return tpl.format(title=title)
    return f"As the {role.lower()}, I'll handle the part of '{title}' that needs my expertise."


# ---------------------------------------------------------------------------
# Optional LLM voice (small free model)
# ---------------------------------------------------------------------------
async def _llm_line(agent_id: str, agent_role: str, model: str, task: dict) -> str | None:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        return None
    try:
        import httpx
    except Exception:
        return None
    sys_prompt = (
        f"You are agent '{agent_id}' ({agent_role}) in a meeting-room dialog about a task. "
        "Reply with ONE sentence (max 28 words) in your role's voice, no preamble, no quotes."
    )
    user = (
        f"Task title: {task.get('title')}\n"
        f"Description: {(task.get('description') or '')[:400]}\n"
        f"Status: {task.get('status')}"
    )
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": sys_prompt},
                     {"role": "user",   "content": user}],
        "temperature": 0.6,
        "max_tokens": 90,
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post("https://openrouter.ai/api/v1/chat/completions",
                             headers=headers, json=payload)
            if r.status_code >= 400:
                return None
            data = r.json()
            return (data["choices"][0]["message"]["content"] or "").strip().strip('"').strip()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Main composer
# ---------------------------------------------------------------------------
async def compose_dialog(task: dict) -> list[dict]:
    """Return a list of {agent_id, role, text, ts} lines for the task."""
    title  = task.get("title") or "(untitled)"
    agents_ids: list[str] = list(task.get("assigned_agents") or [])

    catalog = _reg.load_all()
    # Always include orchestrator at the bookends.
    orchestrator = catalog.get("semeclaw")
    orch_id   = "semeclaw"
    orch_role = orchestrator.role if orchestrator else "Orchestrator"

    lines: list[dict] = []
    now = lambda: datetime.now(timezone.utc).isoformat()

    # Scene-setter
    scene = f"Team — task on the table: '{title}'. Status is {task.get('status', 'open')}. Quick passes please."
    lines.append({
        "agent_id": orch_id, "role": orch_role,
        "text": scene, "audio_url": _audio_url(scene, orch_id),
        "ts": now(),
    })

    # Per-agent line
    for aid in agents_ids:
        a = catalog.get(aid)
        if not a:
            continue
        # Try LLM with the agent's preferred model, fall back to template.
        model = ""
        for pref in (a.model_preference or []):
            if pref.startswith("openrouter:"):
                model = pref.split(":", 1)[1]
                break
        text = (await _llm_line(aid, a.role, model, task)) if model else None
        if not text:
            text = _template_line(aid, a.role, title)
        lines.append({"agent_id": aid, "role": a.role, "text": text,
                      "audio_url": _audio_url(text, aid), "ts": now()})

    # Closing summary
    next_step = "kick off in priority order — research first, then scrape, then write" \
        if any(x in agents_ids for x in ("research", "writer")) \
        else "assign owners and re-sync in 24h"
    closer = f"Decision: {next_step}. I'll re-cap once we have intervention #3 or a finish signal."
    lines.append({
        "agent_id": orch_id, "role": orch_role,
        "text": closer, "audio_url": _audio_url(closer, orch_id),
        "ts": now(),
    })

    return lines
