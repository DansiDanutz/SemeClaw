"""Agent definition and manifest routes."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from war_room.dashboard.routes.deps import (
    AGENTS_DIR,
    APP_VERSION,
    SEMECLAW_API_KEY,
    SEMECLAW_PUBLIC_URL,
    SEMECLAW_TENANT_ID,
    _DEMO_AGENTS,
    _ELEVEN_VOICES,
    _PROTECTED_WRITE_PATHS,
    MEETING_RETENTION_HOURS,
    REPORT_RETENTION_HOURS,
)

router = APIRouter(tags=["agents"])


@router.get("/api/agent/manifest")
async def api_agent_manifest():
    """Describe what this SemeClaw agent can do."""
    auth_required = bool(SEMECLAW_API_KEY)
    return JSONResponse({
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
    })


@router.get("/api/agents")
async def api_agents():
    agents = {}
    for md_file in AGENTS_DIR.glob("*.md"):
        agent_id = md_file.stem
        text = md_file.read_text(encoding="utf-8")
        name = agent_id.capitalize()
        for line in text.splitlines():
            if line.startswith("name:"):
                name = line.split(":", 1)[1].strip()
                break
        agents[agent_id] = {"id": agent_id, "name": name}
    # Demo mode: append demo agents so they appear in the dashboard immediately
    for demo_agent in _DEMO_AGENTS:
        agents[demo_agent["id"]] = demo_agent
    return JSONResponse(agents)
