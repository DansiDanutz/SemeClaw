"""Intervention loop — user comment → agent replies → orchestrator final decision.

Flow per dialog version (capped at 3 interventions, per agents/semeclaw.md):

    intervene(task, dialog, comment) ->
        turn 1 or 2:  agent replies routed to assigned agents
        turn 3:       agent replies + orchestrator strict-JSON decision
                      task is patched, dialog v(n+1) is composed,
                      previous dialog is marked superseded_by the new one,
                      and (optionally) the patched task is pushed back to its source.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

from war_room.agents import registry as _reg
from . import _db, dialog as _dialog


MAX_INTERVENTIONS = 3
_now = lambda: datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Per-agent reply (LLM with template fallback — same pattern as compose_dialog)
# ---------------------------------------------------------------------------
async def _agent_reply(agent_id: str, agent_role: str, model: str,
                       task: dict, comment: str) -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key and model:
        try:
            import httpx
            sys_prompt = (
                f"You are agent '{agent_id}' ({agent_role}) responding to a user comment "
                "during a SemeClaw meeting room. Reply in ONE sentence (max 32 words), "
                "in your role's voice. Acknowledge the comment, take a position, no preamble."
            )
            user = (
                f"Task: {task.get('title')}\n"
                f"Status: {task.get('status')}\n"
                f"User comment: {comment}"
            )
            async with httpx.AsyncClient(timeout=20.0) as c:
                r = await c.post("https://openrouter.ai/api/v1/chat/completions",
                                 headers={"Authorization": f"Bearer {key}",
                                          "Content-Type": "application/json"},
                                 json={"model": model,
                                       "messages": [{"role": "system", "content": sys_prompt},
                                                    {"role": "user", "content": user}],
                                       "temperature": 0.5, "max_tokens": 100})
                if r.status_code < 400:
                    txt = (r.json()["choices"][0]["message"]["content"] or "").strip().strip('"')
                    if txt:
                        return txt
        except Exception:
            pass
    # Deterministic fallback
    return (f"Noted — I hear you on '{comment[:80]}'. "
            f"As the {agent_role.lower()}, my next move accounts for it.")


async def _gather_replies(task: dict, comment: str) -> list[dict]:
    catalog = _reg.load_all()
    out: list[dict] = []
    for aid in (task.get("assigned_agents") or []):
        a = catalog.get(aid)
        if not a:
            continue
        model = next((p.split(":", 1)[1] for p in (a.model_preference or [])
                      if p.startswith("openrouter:")), "")
        text = await _agent_reply(aid, a.role, model, task, comment)
        out.append({"agent_id": aid, "role": a.role, "text": text, "ts": _now()})
    return out


# ---------------------------------------------------------------------------
# Orchestrator final decision (strict JSON parse with safe fallback)
# ---------------------------------------------------------------------------
_DECISION_SCHEMA_HINT = """\
Return ONLY valid JSON in exactly this shape (no prose, no code fences):
{
  "task_patch": {
    "title": "...",
    "description": "...",
    "assigned_agents": ["research", "writer"],
    "status": "in_progress | needs_review | done"
  },
  "rationale": "1-2 sentences for the audit log",
  "dialog_brief": "the seed prompt for the new dialog v2"
}"""


def _safe_json(raw: str) -> dict | None:
    if not raw:
        return None
    # Strip code fences if the model added them
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
    # Find first '{' and last '}' as a heuristic recovery
    try:
        return json.loads(raw)
    except Exception:
        a, b = raw.find("{"), raw.rfind("}")
        if a >= 0 and b > a:
            try:
                return json.loads(raw[a:b + 1])
            except Exception:
                return None
    return None


async def orchestrator_decide(task: dict, all_lines: list[dict],
                              interventions: list[dict]) -> dict:
    """Run the SemeClaw orchestrator to produce a strict-JSON decision."""
    catalog = _reg.load_all()
    orch = catalog.get("semeclaw")
    model = next((p.split(":", 1)[1] for p in ((orch.model_preference if orch else []) or [])
                  if p.startswith("openrouter:")), "")
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()

    # Compact context for the model
    ctx = {
        "task": {k: task.get(k) for k in
                 ("id", "title", "description", "status", "assigned_agents")},
        "dialog_lines": [{"agent": l.get("agent_id"), "text": l.get("text")}
                         for l in (all_lines or [])],
        "interventions": [{"turn": i.get("turn_index"),
                           "user": i.get("user_comment"),
                           "replies": [{"agent": r.get("agent_id"), "text": r.get("text")}
                                       for r in (i.get("agent_replies") or [])]}
                          for i in (interventions or [])],
    }

    decision: dict | None = None
    if key and model:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=30.0) as c:
                r = await c.post("https://openrouter.ai/api/v1/chat/completions",
                                 headers={"Authorization": f"Bearer {key}",
                                          "Content-Type": "application/json"},
                                 json={"model": model,
                                       "messages": [
                                           {"role": "system",
                                            "content": "You are SemeClaw, the orchestrator. "
                                                       "Synthesize the dialog and 3 interventions "
                                                       "into a final task update. " + _DECISION_SCHEMA_HINT},
                                           {"role": "user",
                                            "content": json.dumps(ctx, ensure_ascii=False)},
                                       ],
                                       "temperature": 0.2, "max_tokens": 500,
                                       "response_format": {"type": "json_object"}})
                if r.status_code < 400:
                    decision = _safe_json(r.json()["choices"][0]["message"]["content"])
        except Exception:
            decision = None

    if not decision:
        # Deterministic fallback: keep title/agents, advance status, summarize comments
        last_comment = (interventions[-1].get("user_comment") if interventions else "") or ""
        decision = {
            "task_patch": {
                "title": task.get("title"),
                "description": (task.get("description") or "") +
                               (f"\n\n[v2 update] {last_comment}" if last_comment else ""),
                "assigned_agents": task.get("assigned_agents") or [],
                "status": "needs_review",
            },
            "rationale": "Auto-derived (no LLM available): user pushed back on the original plan; "
                         "moved to needs_review for explicit human approval.",
            "dialog_brief": f"Re-open the discussion on '{task.get('title')}' addressing: {last_comment[:160]}",
        }
    return decision


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
async def intervene(task_id: str, comment: str) -> dict:
    """Record a user comment on the latest dialog. On turn 3, runs the orchestrator,
    patches the task, composes dialog v(n+1), and (optionally) writes back to source.

    Returns:
      {ok, turn_index, agent_replies, orchestrator_decision?, new_dialog?, writeback?}
    """
    task = await _db.get_task(task_id)
    if not task:
        return {"ok": False, "error": "task not found"}

    dialog = await _db.latest_dialog(task_id)
    if not dialog:
        # Auto-generate v1 if missing — interventions need a dialog to attach to
        lines = await _dialog.compose_dialog(task)
        dialog = await _db.insert_dialog(task_id, version=1, lines=lines)

    prior = await _db.list_interventions(dialog["id"])
    if len(prior) >= MAX_INTERVENTIONS:
        return {"ok": False, "error": f"intervention cap reached ({MAX_INTERVENTIONS}); "
                                       "regenerate the dialog first"}

    turn = len(prior) + 1
    replies = await _gather_replies(task, comment)

    decision = None
    new_dialog = None
    writeback_result = None

    if turn >= MAX_INTERVENTIONS:
        # Build full intervention list (including the current one) for context
        all_interventions = prior + [{
            "turn_index": turn, "user_comment": comment, "agent_replies": replies,
        }]
        decision = await orchestrator_decide(task, dialog.get("lines") or [], all_interventions)

        # Apply the patch
        patch = decision.get("task_patch", {}) or {}
        db_patch: dict = {}
        if "title" in patch:           db_patch["title"]           = patch["title"]
        if "description" in patch:     db_patch["description"]     = patch["description"]
        if "assigned_agents" in patch: db_patch["assigned_agents"] = patch["assigned_agents"]
        if "status" in patch:          db_patch["status"]          = patch["status"]
        if db_patch:
            await _db.patch_task(task_id, db_patch)
            task = {**task, **db_patch}

        # Compose dialog v(n+1) seeded with the brief
        seed_task = {**task, "description": (decision.get("dialog_brief") or task.get("description"))}
        new_lines = await _dialog.compose_dialog(seed_task)
        new_dialog = await _db.insert_dialog(task_id, version=dialog["version"] + 1, lines=new_lines)
        await _db.supersede_dialog(dialog["id"], new_dialog["id"])

        # Optional write-back to the source adapter
        try:
            from . import writeback as _wb
            writeback_result = await _wb.push_to_source(task)
        except Exception as e:
            writeback_result = {"ok": False, "error": str(e)}

    saved = await _db.insert_intervention(
        task_id=task_id, dialog_id=dialog["id"], turn_index=turn,
        user_comment=comment, agent_replies=replies,
        orchestrator_decision=decision,
    )

    return {
        "ok": True,
        "turn_index": turn,
        "intervention": saved,
        "agent_replies": replies,
        "orchestrator_decision": decision,
        "new_dialog": new_dialog,
        "writeback": writeback_result,
    }
