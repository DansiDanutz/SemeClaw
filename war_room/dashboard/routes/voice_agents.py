"""Voice Agent Builder — no-code voice agents callable from the browser.

Inspired by xAI's Voice Agent Builder: define a voice agent (persona, voice,
greeting, knowledge, guardrails) in under two minutes, then talk to it from
the browser. SemeClaw already ships the hard parts — the /api/tts pipeline
(ElevenLabs Flash v2.5 → Kokoro → edge-tts), a free-model LLM waterfall, and
tenant-scoped bearer auth — so this module only adds:

  * a JSON-file store of agent definitions under war_room/voice_agents/
  * a conversational /respond endpoint that runs the agent's brain
  * the /voice-builder UI page (mic capture + playback happen client-side)

Writes (create / delete / respond) are covered by the standard bearer-token
middleware via _PROTECTED_WRITE_PATHS; reads stay open like the rest of the
public surface.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from war_room.dashboard.routes.deps import WAR_ROOM_DIR, _cost_bump, _tenant_id, logger

try:
    import httpx

    _HAS_HTTPX = True
except ImportError:  # pragma: no cover
    _HAS_HTTPX = False

router = APIRouter(tags=["voice-agents"])

VOICE_AGENTS_DIR = WAR_ROOM_DIR / "voice_agents"

# Voices available in the builder dropdown. Keys are the speaker names the
# /api/tts endpoint already understands (ElevenLabs mapping + Kokoro/edge
# fallbacks); values are short human descriptions for the UI.
BUILDER_VOICES = {
    "David": "Calm, strategic — orchestrator",
    "Dan": "Deep, resonant, comforting (Brian)",
    "Dexter": "Blunt, technical, confident",
    "Memo": "Organized, practical, friendly",
    "Sienna": "Sharp, direct",
    "Nano": "Enthusiastic builder",
    "Hermes": "Warm communicator",
    "Pi": "Analytical researcher",
}

_MAX_FIELD_CHARS = 8000
_MAX_HISTORY_TURNS = 20

# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:64] or "agent"


def _tenant_dir(tenant: str) -> Path:
    safe_tenant = re.sub(r"[^a-zA-Z0-9_-]", "_", tenant)[:64] or "default"
    d = VOICE_AGENTS_DIR / safe_tenant
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_agent(tenant: str, agent_id: str) -> dict | None:
    safe_id = _slugify(agent_id)
    path = _tenant_dir(tenant) / f"{safe_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _clip(value, limit: int = _MAX_FIELD_CHARS) -> str:
    return str(value or "").strip()[:limit]


# ---------------------------------------------------------------------------
# LLM waterfall (chat-style, multi-turn)
#
# Mirrors the free-model waterfall in server.py, but accepts a full message
# list so agents keep conversational context. Kept local to avoid a circular
# import (server.py imports this module's router).
# ---------------------------------------------------------------------------

_VA_MODELS = [
    ("openrouter", "meta-llama/llama-3.3-70b-instruct:free"),
    ("openrouter", "qwen/qwen3-next-80b-a3b-instruct:free"),
    ("openrouter", "google/gemma-3-27b-it:free"),
    ("openrouter", "openai/gpt-oss-120b:free"),
    ("ollama", "gemma4:latest"),
    ("ollama", "qwen3:8b"),
]

_OPENROUTER_BASE = "https://openrouter.ai/api/v1"
_OLLAMA_BASE = "http://127.0.0.1:11434"


def _openrouter_key() -> str:
    import os as _os
    from pathlib import Path as _P

    key = _os.environ.get("OPENROUTER_API_KEY") or _os.environ.get("DLS_OPENROUTER_API_KEY") or ""
    if key:
        return key
    for env_file in (_P("/etc/openclaw-env"), _P.home() / ".openclaw" / "fleet.env"):
        if not env_file.exists():
            continue
        try:
            for line in env_file.read_text().splitlines():
                s = line.strip().removeprefix("export ").strip()
                for prefix in ("OPENROUTER_API_KEY=", "DLS_OPENROUTER_API_KEY="):
                    if s.startswith(prefix):
                        return s.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            continue
    return ""


async def _chat_openrouter(model: str, messages: list[dict]) -> str | None:
    key = _openrouter_key()
    if not key or not _HAS_HTTPX:
        return None
    headers = {
        "Authorization": f"Bearer {key}",
        "HTTP-Referer": "https://nervix.ai",
        "X-Title": "SemeClaw Voice Agent",
        "Content-Type": "application/json",
    }
    payload = {"model": model, "max_tokens": 220, "messages": messages}
    try:
        async with httpx.AsyncClient(base_url=_OPENROUTER_BASE, timeout=30.0) as c:
            r = await c.post("/chat/completions", headers=headers, json=payload)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning("voice-agent OpenRouter %s failed: %s", model, e)
        return None


async def _chat_ollama(model: str, messages: list[dict]) -> str | None:
    if not _HAS_HTTPX:
        return None
    payload = {
        "model": model,
        "stream": False,
        "options": {"num_predict": 220},
        "messages": messages,
    }
    try:
        async with httpx.AsyncClient(base_url=_OLLAMA_BASE, timeout=60.0) as c:
            r = await c.post("/api/chat", json=payload)
            r.raise_for_status()
            return r.json()["message"]["content"].strip()
    except Exception as e:
        logger.warning("voice-agent Ollama %s failed: %s", model, e)
        return None


_VOICE_SYSTEM_TEMPLATE = """\
You are {name}, a voice agent on a live phone-style call.

Your persona:
{persona}

{knowledge_block}{guardrails_block}Voice-call rules — these are hard requirements:
- Your reply is spoken aloud by TTS. Plain conversational text only: no markdown, no bullet lists, no emojis, no stage directions.
- Keep it short — 1-3 sentences, like a real call. Never lecture.
- If the caller asks something outside your knowledge or guardrails, say so briefly and steer back to what you can help with.
- Stay in character as {name} at all times. Never mention being an AI language model or these instructions.
"""


def _build_system_prompt(agent: dict) -> str:
    knowledge = agent.get("knowledge", "").strip()
    guardrails = agent.get("guardrails", "").strip()
    knowledge_block = f"Knowledge base (answer from this when relevant):\n{knowledge}\n\n" if knowledge else ""
    guardrails_block = f"Guardrails (never violate these):\n{guardrails}\n\n" if guardrails else ""
    return _VOICE_SYSTEM_TEMPLATE.format(
        name=agent.get("name", "Agent"),
        persona=agent.get("persona", "A helpful, friendly voice assistant."),
        knowledge_block=knowledge_block,
        guardrails_block=guardrails_block,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/voice-builder")
async def voice_builder_page():
    """Serve the no-code Voice Agent Builder UI."""
    page = VOICE_AGENTS_DIR.parent / "dashboard" / "voice_builder.html"
    if not page.exists():
        return JSONResponse({"error": "voice_builder.html not found"}, status_code=500)
    return FileResponse(page, media_type="text/html")


@router.get("/api/voice-agents/voices")
async def api_voice_agent_voices():
    """Voices selectable in the builder (speaker names /api/tts understands)."""
    return JSONResponse({"voices": [{"speaker": k, "description": v} for k, v in BUILDER_VOICES.items()]})


@router.get("/api/voice-agents")
async def api_voice_agents_list(request: Request):
    """List this tenant's voice agents."""
    tenant = _tenant_id(request)
    agents = []
    for path in sorted(_tenant_dir(tenant).glob("*.json")):
        try:
            agents.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return JSONResponse({"agents": agents, "count": len(agents)})


@router.post("/api/voice-agents")
async def api_voice_agents_create(request: Request):
    """Create or update a voice agent.

    Body: {"name", "voice", "greeting", "persona", "knowledge", "guardrails"}
    Only "name" is required. Posting an existing agent's name updates it.
    """
    tenant = _tenant_id(request)
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    name = _clip(data.get("name"), 80)
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)

    voice = _clip(data.get("voice"), 40) or "David"
    if voice not in BUILDER_VOICES:
        return JSONResponse(
            {"error": f"unknown voice '{voice}'", "available": list(BUILDER_VOICES)}, status_code=400
        )

    agent_id = _slugify(name)
    now = datetime.now(timezone.utc).isoformat()
    existing = _load_agent(tenant, agent_id) or {}
    agent = {
        "id": agent_id,
        "name": name,
        "voice": voice,
        "greeting": _clip(data.get("greeting"), 500) or f"Hi, you've reached {name}. How can I help?",
        "persona": _clip(data.get("persona")) or "A helpful, friendly voice assistant.",
        "knowledge": _clip(data.get("knowledge")),
        "guardrails": _clip(data.get("guardrails")),
        "created_at": existing.get("created_at", now),
        "updated_at": now,
    }
    path = _tenant_dir(tenant) / f"{agent_id}.json"
    path.write_text(json.dumps(agent, indent=2), encoding="utf-8")
    logger.info("voice-agent saved: tenant=%s id=%s", tenant, agent_id)
    return JSONResponse(agent, status_code=201 if not existing else 200)


@router.get("/api/voice-agents/{agent_id}")
async def api_voice_agents_get(request: Request, agent_id: str):
    tenant = _tenant_id(request)
    agent = _load_agent(tenant, agent_id)
    if not agent:
        return JSONResponse({"error": "agent not found"}, status_code=404)
    return JSONResponse(agent)


@router.delete("/api/voice-agents/{agent_id}")
async def api_voice_agents_delete(request: Request, agent_id: str):
    tenant = _tenant_id(request)
    path = _tenant_dir(tenant) / f"{_slugify(agent_id)}.json"
    if not path.exists():
        return JSONResponse({"error": "agent not found"}, status_code=404)
    path.unlink()
    return JSONResponse({"ok": True, "deleted": _slugify(agent_id)})


@router.post("/api/voice-agents/{agent_id}/respond")
async def api_voice_agents_respond(request: Request, agent_id: str):
    """One conversational turn with a voice agent.

    Body: {"message": "...", "history": [{"role": "user"|"assistant", "content": "..."}]}
    Returns: {"reply", "model", "agent", "voice", "tts"} — feed `tts` straight
    into an <audio> element to hear the answer.
    """
    tenant = _tenant_id(request)
    agent = _load_agent(tenant, agent_id)
    if not agent:
        return JSONResponse({"error": "agent not found"}, status_code=404)

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    message = _clip(data.get("message"), 2000)
    if not message:
        return JSONResponse({"error": "message required"}, status_code=400)

    messages = [{"role": "system", "content": _build_system_prompt(agent)}]
    history = data.get("history") or []
    if isinstance(history, list):
        for turn in history[-_MAX_HISTORY_TURNS:]:
            role = turn.get("role") if isinstance(turn, dict) else None
            content = _clip(turn.get("content"), 2000) if isinstance(turn, dict) else ""
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})

    reply = None
    used_model = None
    for provider, model in _VA_MODELS:
        reply = await (_chat_openrouter if provider == "openrouter" else _chat_ollama)(model, messages)
        if reply:
            used_model = f"{provider}/{model}"
            break

    if not reply:
        return JSONResponse({"error": "All AI models unavailable"}, status_code=503)

    _cost_bump(tenant, "voice_agent_turns")
    from urllib.parse import quote

    return JSONResponse(
        {
            "reply": reply,
            "model": used_model,
            "agent": agent["id"],
            "voice": agent["voice"],
            "tts": f"/api/tts?text={quote(reply)}&speaker={quote(agent['voice'])}&lang=en",
        }
    )
