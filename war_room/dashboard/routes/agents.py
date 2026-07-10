"""Agent definition and manifest routes."""

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from war_room.dashboard.routes.deps import (
    _DEMO_AGENTS,
    _ELEVEN_VOICES,
    _PROTECTED_WRITE_PATHS,
    AGENTS_DIR,
    APP_VERSION,
    MEETING_RETENTION_HOURS,
    REPORT_RETENTION_HOURS,
    SEMECLAW_API_KEY,
    SEMECLAW_MANIFEST_URL,
    SEMECLAW_PUBLIC_URL,
    SEMECLAW_TENANT_ID,
)

router = APIRouter(tags=["agents"])


@router.get("/api/agent/update-check")
async def api_agent_update_check():
    """Poll the remote R2 manifest and report whether a newer version exists."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(SEMECLAW_MANIFEST_URL)
            r.raise_for_status()
            data = r.json()
        latest = data.get("latest")
        updates = data.get("updates", [])
        entry = updates[0] if updates else {}
        return {
            "current": APP_VERSION,
            "latest": latest,
            "update_available": latest is not None and latest != APP_VERSION,
            "release_url": entry.get("release_url", ""),
            "image": entry.get("image", ""),
            "date": entry.get("date", ""),
            "manifest_url": SEMECLAW_MANIFEST_URL,
        }
    except Exception as exc:
        return {
            "current": APP_VERSION,
            "latest": None,
            "update_available": False,
            "error": str(exc),
            "manifest_url": SEMECLAW_MANIFEST_URL,
        }


@router.get("/api/agent/manifest")
async def api_agent_manifest():
    """Describe what this SemeClaw agent can do."""
    auth_required = bool(SEMECLAW_API_KEY)
    return JSONResponse(
        {
            "id": "semeclaw-war-room",
            "name": "SemeClaw War Room",
            "version": APP_VERSION,
            "tenant": SEMECLAW_TENANT_ID,
            "public_url": SEMECLAW_PUBLIC_URL,
            "description": (
                "Cinematic AI agent meeting room. Converts any task report "
                "into a scripted multi-agent dialogue with voice, user "
                "interjections (2-question budget), live recalibration, "
                "and task re-analysis."
            ),
            "capabilities": [
                "meeting.script",
                "meeting.audio",
                "meeting.redirect",
                "meeting.replan",
                "meeting.finalize",
                "meeting.pin",
                "meeting.share",
                "meeting.events.sse",
                "reports.list",
                "reports.content",
                "reports.create",
                "reports.upload",
                "reports.delete",
                "tts.synthesize",
                "embed.iframe",
                "embed.widget",
                "webhooks.register",
                "metrics.prometheus",
                "tenants.isolation",
                "paperclip.trigger",
                "paperclip.card",
                "templates.list",
                "templates.use",
                "voices.override",
                "costs.ledger",
                "layout.theater",
                "voices.clone",
                "transcripts.srt",
                "transcripts.html",
                "billing.stripe",
                "integrations.slack",
                "integrations.github",
                "skills.registry",
                "meeting.agents",
                "meeting.inject",
                "compound.engineering",
                "voice.agents.builder",
                "voice.agents.respond",
            ],
            "endpoints": {
                "health": "/api/agent/health",
                "manifest": "/api/agent/manifest",
                "reports": "/api/reports",
                "report": "/api/reports/content?name={name}",
                "report_create": "POST /api/reports",
                "report_upload": "POST /api/reports/upload",
                "report_delete": "DELETE /api/reports?name={name}",
                "script": "/api/meeting/script?name={name}&lang=en",
                "audio": "/api/meeting/audio?name={name}",
                "redirect": "/api/meeting/redirect",
                "replan": "/api/meeting/replan",
                "finalize": "/api/meeting/finalize",
                "pin": "/api/meeting/pin?name={name}",
                "unpin": "/api/meeting/unpin?file={file}&name={name}",
                "list": "/api/meeting/list",
                "share": "GET /api/meeting/share?name={name}",
                "events_sse": "/api/events?tenant={id}&events={csv}",
                "tts": "/api/tts?text={text}&speaker={speaker}&lang=en",
                "embed_html": "/embed?meeting={name}&v=2",
                "embed_js": "/embed.js",
                "metrics": "/metrics",
                "webhooks": "/api/webhooks",
                "paperclip_card": "/api/paperclip/agent-card",
                "paperclip_trigger": "POST /api/paperclip/trigger",
                "skills_list": "/api/agents/skills",
                "skill_detail": "/api/agents/skills/{skill_id}",
                "meeting_agents": "/api/meeting/agents?name={name}",
                "inject": "POST /api/meeting/inject",
                "voice_builder_ui": "/voice-builder",
                "voice_agents": "/api/voice-agents",
                "voice_agent_create": "POST /api/voice-agents",
                "voice_agent_respond": "POST /api/voice-agents/{agent_id}/respond",
                "voice_agent_voices": "/api/voice-agents/voices",
            },
            "auth": {
                "required_for_writes": auth_required,
                "scheme": "bearer" if auth_required else "none",
                "header": "Authorization: Bearer <SEMECLAW_API_KEY>" if auth_required else None,
                "protected_paths": list(_PROTECTED_WRITE_PATHS) if auth_required else [],
            },
            "tts": {
                "engine": "elevenlabs-flash-v2.5 + edge-tts fallback",
                "languages": ["en"],
                "voice_map_size": len(_ELEVEN_VOICES),
            },
            "retention": {
                "meetings_hours": MEETING_RETENTION_HOURS,
                "reports_hours": REPORT_RETENTION_HOURS,
                "pin_to_save": True,
            },
            "layouts": ["v1-flat", "v2-orbital"],
            "meeting_budget": {
                "max_user_questions_per_meeting": 2,
                "recalibration": "orchestrator/hermes",
                "finalize_verdict_line": True,
            },
        }
    )


@router.get("/api/agents")
async def api_agents(include: str = "all"):
    """Return all registered agents.

    Query: include=core | adapters | all (default)
    Reads from war_room.agents._registry — single source of truth.
    """
    try:
        from war_room.agents import registry as _agent_registry

        items = list(_agent_registry.load_all().values())
        if include == "core":
            items = [a for a in items if a.core]
        elif include == "adapters":
            items = [a for a in items if a.is_adapter]
        agents = {a.id: a.to_dict() for a in items}
    except Exception as e:
        # If registry can't load, fall back to legacy scan so the dashboard never breaks
        agents = {}
        for md_file in AGENTS_DIR.glob("*.md"):
            agent_id = md_file.stem
            agents[agent_id] = {"id": agent_id, "name": agent_id.capitalize(), "registry_error": str(e)}
    # Always append demo agents so a fresh clone shows a populated dashboard
    for demo_agent in _DEMO_AGENTS:
        agents.setdefault(demo_agent["id"], demo_agent)
    return JSONResponse(agents)


@router.get("/api/agents/adapters/{adapter_id}/agents")
async def api_adapter_agents(adapter_id: str):
    """Phase C: discover agents inside a connected adapter workspace.

    Calls the adapter's `agents_path` (from the .md frontmatter), substituting
    `{workspace_id}` / `{company_id}` from env. Returns the raw payload so
    SemeClaw can route tasks to the user's *own* agents instead of (or in
    addition to) the built-in 4.
    """
    import os as _os

    try:
        from war_room.agents import registry as _agent_registry
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    a = _agent_registry.get(adapter_id) or _agent_registry.get(f"adapter_{adapter_id}")
    if not a or not a.is_adapter:
        return JSONResponse({"ok": False, "error": f"adapter '{adapter_id}' not found"}, status_code=404)

    meta = a.adapter or {}
    if meta.get("protocol") != "http":
        return JSONResponse(
            {"ok": False, "error": f"adapter protocol {meta.get('protocol')!r} is not http"}, status_code=400
        )

    base = _os.environ.get(meta.get("base_url_env", ""), "").strip() or meta.get("base_url", "")
    key = _os.environ.get(meta.get("api_key_env", ""), "").strip()
    path = meta.get("agents_path", "")
    if not (base and key and path):
        missing = [k for k in (meta.get("required_env") or []) if not _os.environ.get(k)]
        return JSONResponse({"ok": False, "error": "adapter not configured", "missing_env": missing}, status_code=409)

    # Substitute path templates from env (e.g. {workspace_id} <- MOLTICA_WORKSPACE_ID)
    for placeholder in ("workspace_id", "company_id", "tenant_id"):
        env_key = f"{adapter_id.upper()}_{placeholder.upper()}"
        val = _os.environ.get(env_key, "")
        path = path.replace("{" + placeholder + "}", val)

    url = base.rstrip("/") + path
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(url, headers={"Authorization": f"Bearer {key}"})
            if r.status_code >= 400:
                return JSONResponse(
                    {"ok": False, "error": f"upstream HTTP {r.status_code}", "body": r.text[:240]}, status_code=502
                )
            data = r.json()
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=502)

    # Normalize: try common shapes (data, agents, items) → list[dict]
    items = data.get("agents") or data.get("data") or data.get("items") or data
    if not isinstance(items, list):
        items = []
    return JSONResponse({"ok": True, "adapter": a.id, "url": url, "count": len(items), "agents": items})


@router.get("/api/agents/adapters/status")
async def api_adapter_status():
    """Per-adapter status: which env vars are set, is the adapter ready to call?"""
    import os as _os

    try:
        from war_room.agents import browser_search as _bs
        from war_room.agents import registry as _agent_registry
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    out = {"adapters": [], "browser_search": _bs.status()}
    for a in _agent_registry.adapters():
        meta = a.adapter or {}
        required = meta.get("required_env", []) or []
        missing = [k for k in required if not _os.environ.get(k)]
        out["adapters"].append(
            {
                "id": a.id,
                "name": a.name,
                "kind": meta.get("protocol", "http"),
                "required_env": required,
                "missing_env": missing,
                "ready": len(missing) == 0,
            }
        )
    return JSONResponse(out)
