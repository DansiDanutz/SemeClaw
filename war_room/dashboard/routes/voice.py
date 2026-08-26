"""Voice surface — TTS, STT, per-tenant voice maps, and voice cloning.

Extracted from server.py (Phase 3.1 of the improvement goal). Owns:

    GET  /api/voices/map     — effective {speaker → voice} map for the tenant
    PUT  /api/voices/map     — set/clear per-tenant overrides
    POST /api/voices/clone   — ElevenLabs Instant Voice Clone + registration
    GET  /api/tts            — ElevenLabs Flash v2.5 → Kokoro fallback stream
    POST /api/stt            — faster-whisper transcription

Also the voice engine state: the default speaker→voice map, the ElevenLabs
key/model/voice-id cache, and the text naturalizer. `server.py` imports
`_ELEVEN_VOICES` (capabilities payload) and `_LANG_NAMES` (meeting-script
language prompts) from here.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from war_room.dashboard.routes.deps import WAR_ROOM_DIR, _bump, _cost_bump, _tenant_id

logger = logging.getLogger("war_room.dashboard.voice")
router = APIRouter(tags=["voice"])

# ---------------------------------------------------------------------------
# Per-tenant voice overrides
# ---------------------------------------------------------------------------
VOICE_MAP_FILE = WAR_ROOM_DIR / "voice_overrides.json"


def _load_voice_overrides() -> dict:
    if not VOICE_MAP_FILE.exists():
        return {}
    try:
        return json.loads(VOICE_MAP_FILE.read_text())
    except Exception:
        return {}


def _save_voice_overrides(d: dict) -> None:
    VOICE_MAP_FILE.write_text(json.dumps(d, indent=2))


def _resolve_voice_for_tenant(speaker: str, tenant_id: str) -> str:
    """Return the overridden voice for (tenant, speaker) if set, else the default."""
    overrides = _load_voice_overrides()
    tenant_map = overrides.get(tenant_id, {})
    if speaker in tenant_map:
        return tenant_map[speaker]
    # Fallback to global overrides ("default" tenant) then to _ELEVEN_VOICES
    global_map = overrides.get("default", {})
    return global_map.get(speaker, _ELEVEN_VOICES.get(speaker, ""))


# ---------------------------------------------------------------------------
# ElevenLabs Flash v2.5 — premium voice layer (English only). Falls back to
# Kokoro when key is absent. Dan = Brian (Deep, Resonant, Comforting).
# ---------------------------------------------------------------------------
_ELEVEN_VOICES: dict[str, str] = {
    # Primary — Dan = the boss = Brian (Deep, Resonant, Comforting) — American entrepreneur voice
    "Dan": "Brian",
    "User": "Brian",  # user messages read back in Dan's voice
    # Core team
    "David": "Brian",  # deep resonant comforting — same entrepreneur voice as Dan
    "Orchestrator": "Brian",
    "Dexter": "Adam",  # dominant firm — senior dev
    "Memo": "Chris",  # charming down-to-earth — PM
    "Sienna": "Bella",  # professional bright warm — crypto analyst
    "Nano": "Liam",  # energetic — agent creator
    "GSD": "Matilda",  # knowledgable professional — strategist
    "Hermes": "Alice",  # clear engaging educator (British) — messenger
    "Hermes Strategy": "Alice",
    "Pi": "Charlie",  # deep confident (Australian) — senior dev
    "Pi Stability": "Charlie",
    # Extended
    "Discovery": "George",  # warm captivating storyteller (British) — researcher
    "Autoresearch": "Eric",  # smooth trustworthy — analytical
    "Doctor": "Daniel",  # steady broadcaster (British) — clinical
    "DoctorLocal": "Daniel",
    "Monitor": "Gregory",  # tech reviewer — alert SRE
    "Growth": "Jessica",  # playful bright warm — growth hacker
    "Finance": "Lily",  # velvety (British) — measured CFO
    "N8N": "River",  # relaxed neutral informative — automation
    "Teacher": "Sarah",  # mature reassuring — patient teacher
    "Learning": "Sarah",
    "Codex": "Matilda",  # knowledgable professional
    "CodexMax": "Matilda",
    "Xlaude": "Ember",  # energetic confident — premium
    "KiloClaw": "Callum",  # husky trickster — distinctive
    "Claude Code": "Jessica",  # bright helpful
    "OpenClaw": "Roger",  # laid-back casual resonant — agent OS
    "System": "Alice",  # clear neutral British
    "Narrator": "George",  # warm captivating storyteller — narrator
}
_ELEVEN_MODEL = "eleven_flash_v2_5"
_ELEVEN_VOICE_ID_CACHE: dict[str, str] = {}


def _load_elevenlabs_key() -> str | None:
    """Load ELEVENLABS_API_KEY from env, /etc/openclaw-env, or ~/.openclaw/fleet.env."""
    key = os.environ.get("ELEVENLABS_API_KEY") or os.environ.get("XI_API_KEY")
    if key:
        return key
    for env_file in (Path("/etc/openclaw-env"), Path.home() / ".openclaw" / "fleet.env"):
        if not env_file.exists():
            continue
        try:
            for line in env_file.read_text().splitlines():
                line = line.strip().removeprefix("export ").strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() in ("ELEVENLABS_API_KEY", "XI_API_KEY"):
                    v = v.strip().strip('"').strip("'")
                    if v:
                        return v
        except PermissionError:
            continue
    return None


_ELEVEN_KEY = _load_elevenlabs_key()


async def _resolve_eleven_voice_id(client, name: str) -> str | None:
    """Resolve a voice NAME (e.g. 'Bill') to its voice_id, cached."""
    if name in _ELEVEN_VOICE_ID_CACHE:
        return _ELEVEN_VOICE_ID_CACHE[name]
    try:
        resp = await client.get(
            "https://api.elevenlabs.io/v1/voices",
            headers={"xi-api-key": _ELEVEN_KEY},
            timeout=10.0,
        )
        resp.raise_for_status()
        for v in resp.json().get("voices", []):
            full = v.get("name", "")
            short = full.split(" -")[0].strip()
            _ELEVEN_VOICE_ID_CACHE[short] = v.get("voice_id", "")
            _ELEVEN_VOICE_ID_CACHE[full] = v.get("voice_id", "")
        return _ELEVEN_VOICE_ID_CACHE.get(name)
    except Exception as e:
        logger.warning(f"ElevenLabs voice lookup failed: {e}")
        return None


# Language display names — used by meeting-script generation prompts.
_LANG_NAMES: dict[str, str] = {
    "en": "English",
    "ro": "Romanian",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "pt": "Portuguese",
    "it": "Italian",
}


def _naturalize_tts_text(text: str) -> str:
    """Add punctuation-based breathing cues so ElevenLabs sounds more human.

    ElevenLabs Flash v2.5 respects punctuation rhythm:
    - Comma  → short breath (~150ms)
    - Period → medium pause (~350ms)
    - Ellipsis → longer thoughtful pause (~600ms)
    We inject these before common interjections and after sentence fragments
    so agents sound like they're actually thinking, not just reciting.
    """
    import re

    t = text.strip()

    # Expand common interjections to get a natural breath before the next clause
    interjections = {
        r"\bYeah\b": "Yeah,",
        r"\bYeah\.": "Yeah...",
        r"\bHmm\b": "Hmm...",
        r"\bOkay\b": "Okay,",
        r"\bAlright\b": "Alright,",
        r"\bSo\b,": "So,",
        r"\bLook\b,": "Look,",
        r"\bRight\b,": "Right,",
        r"\bWell\b,": "Well,",
        r"\bActually\b,": "Actually,",
        r"\bFair point\.": "Fair point...",
        r"\bGood point\.": "Good point...",
    }
    for pattern, replacement in interjections.items():
        t = re.sub(pattern, replacement, t, count=1)

    # If the turn is short (≤60 chars) and ends without punctuation, add a period
    # so ElevenLabs knows to drop pitch naturally at the end
    if len(t) <= 60 and t and t[-1] not in ".!?,…":
        t += "."

    return t


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("/api/voices/map")
async def api_voices_map(request: Request):
    """Current {speaker → voice} mapping for this tenant. Shows defaults
    overlaid with any custom overrides."""
    tenant = _tenant_id(request)
    overrides = _load_voice_overrides().get(tenant, {})
    merged = {**_ELEVEN_VOICES, **overrides}
    return JSONResponse(
        {
            "tenant_id": tenant,
            "defaults": _ELEVEN_VOICES,
            "overrides": overrides,
            "effective": merged,
        }
    )


@router.put("/api/voices/map")
async def api_voices_map_set(request: Request):
    """Update the voice map for this tenant.
    Body: {"speaker_name": "voice_name", ...}
    Unknown speakers are accepted. To clear a mapping, set value to null."""
    data = await request.json()
    if not isinstance(data, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
    tenant = _tenant_id(request)
    overrides = _load_voice_overrides()
    current = overrides.get(tenant, {})
    for speaker, voice in data.items():
        if voice in (None, ""):
            current.pop(speaker, None)
        else:
            current[speaker] = str(voice)
    overrides[tenant] = current
    _save_voice_overrides(overrides)
    return JSONResponse(
        {
            "ok": True,
            "tenant_id": tenant,
            "overrides": current,
        }
    )


@router.post("/api/voices/clone")
async def api_voices_clone(request: Request):
    """Clone a voice via ElevenLabs Instant Voice Clone and register it for
    this tenant. The new voice_id is immediately usable in /api/tts for the
    speaker mapping the consumer chooses via /api/voices/map.

    Multipart form fields:
        file        — reference audio (.mp3, .wav), 30s-2min recommended
        name        — display name for the clone (e.g. 'Dan Primary')
        description — optional descriptor
        speaker     — optional; if set, auto-registers mapping for tenant
    """
    tenant = _tenant_id(request)
    if not _ELEVEN_KEY:
        return JSONResponse({"error": "ELEVENLABS_API_KEY not configured"}, status_code=503)

    form = await request.form()
    f = form.get("file")
    if not f or not hasattr(f, "read"):
        return JSONResponse({"error": "file field required"}, status_code=400)
    name = (form.get("name") or f.filename or "Cloned Voice").strip()
    description = (form.get("description") or "").strip()
    speaker_map_key = (form.get("speaker") or "").strip()

    # Stream to ElevenLabs IVC endpoint
    try:
        audio_bytes = await f.read()
        files = {"files": (f.filename or "sample.mp3", audio_bytes, "audio/mpeg")}
        data = {"name": name, "description": description}
        async with httpx.AsyncClient(timeout=60.0) as c:
            resp = await c.post(
                "https://api.elevenlabs.io/v1/voices/add",
                headers={"xi-api-key": _ELEVEN_KEY, "accept": "application/json"},
                files=files,
                data=data,
            )
        if resp.status_code != 200:
            return JSONResponse({"error": f"ElevenLabs {resp.status_code}: {resp.text[:300]}"}, status_code=502)
        voice = resp.json()
    except Exception as e:
        logger.warning(f"voice clone failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

    voice_id = voice.get("voice_id") or voice.get("voice_id_")
    if not voice_id:
        return JSONResponse({"error": "no voice_id returned", "upstream": voice}, status_code=502)

    # Cache it in our in-process voice_id map so /api/tts can use it by name
    _ELEVEN_VOICE_ID_CACHE[name] = voice_id

    # Optionally bind to a speaker mapping for this tenant
    if speaker_map_key:
        overrides = _load_voice_overrides()
        current = overrides.get(tenant, {})
        current[speaker_map_key] = name
        overrides[tenant] = current
        _save_voice_overrides(overrides)

    # Imported lazily — server.py owns the webhook dispatcher and imports
    # this module at startup, so a module-level import would be circular.
    from war_room.dashboard.server import _dispatch_webhook

    await _dispatch_webhook(
        "voice.cloned",
        {
            "voice_id": voice_id,
            "name": name,
            "tenant_id": tenant,
            "speaker_bound": speaker_map_key or None,
        },
    )

    return JSONResponse(
        {
            "ok": True,
            "voice_id": voice_id,
            "name": name,
            "tenant_id": tenant,
            "bound_to_speaker": speaker_map_key or None,
        }
    )


@router.get("/api/tts")
async def api_tts(request: Request, text: str, speaker: str = "", lang: str = "en"):
    """Stream MP3 audio for a given text + speaker using ElevenLabs or Kokoro neural voices.

    Returns: audio/mpeg binary — play directly with <audio> or AudioContext.
    Honours per-tenant voice overrides from /api/voices/map.
    Counts tts_chars against the tenant's cost ledger.
    """
    _bump("tts_requests")
    tenant = _tenant_id(request)

    # Naturalize text for more human-sounding delivery (English only)
    if not lang or lang == "en":
        text = _naturalize_tts_text(text or "")

    _cost_bump(tenant, "tts_chars", len(text or ""))

    # Resolve effective voice (tenant override wins)
    effective_voice_name = _resolve_voice_for_tenant(speaker, tenant)

    # ── ElevenLabs Flash v2.5 path (English only, key required) ────────────
    if _ELEVEN_KEY and (not lang or lang == "en") and effective_voice_name:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                voice_id = await _resolve_eleven_voice_id(client, effective_voice_name)
                if voice_id:
                    resp = await client.post(
                        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                        headers={
                            "xi-api-key": _ELEVEN_KEY,
                            "accept": "audio/mpeg",
                            "content-type": "application/json",
                        },
                        params={"output_format": "mp3_44100_128"},
                        json={
                            "text": text,
                            "model_id": _ELEVEN_MODEL,
                            # Lower stability = more expressive, emotional, human-sounding.
                            # Style > 0 adds emphasis and variation. Boost keeps voice identity.
                            "voice_settings": {
                                "stability": 0.38,
                                "similarity_boost": 0.82,
                                "style": 0.35,
                                "use_speaker_boost": True,
                            },
                        },
                    )
                    if resp.status_code == 200 and resp.content:
                        return Response(
                            content=resp.content,
                            media_type="audio/mpeg",
                            headers={
                                "Cache-Control": "public, max-age=3600",
                                "X-Speaker": speaker,
                                "X-Voice": effective_voice_name,
                                "X-Tenant": tenant,
                                "X-TTS-Engine": "elevenlabs-flash-v2.5",
                            },
                        )
                    else:
                        logger.warning(f"ElevenLabs {resp.status_code} for {speaker}: {resp.text[:200]}")
        except Exception as e:
            logger.warning(f"ElevenLabs fallback to edge-tts for {speaker}: {e}")

    # ── Kokoro open-source TTS fallback (free, Apache 2.0) ────────────────
    try:
        from war_room.dashboard import kokoro_tts as kt

        mp3_path = kt.synthesize(text, voice=effective_voice_name, agent=speaker)
        if mp3_path and mp3_path.exists():
            return Response(
                content=mp3_path.read_bytes(),
                media_type="audio/mpeg",
                headers={
                    "Cache-Control": "public, max-age=3600",
                    "X-Speaker": speaker,
                    "X-Voice": effective_voice_name,
                    "X-Tenant": tenant,
                    "X-TTS-Engine": "kokoro-82M",
                },
            )
    except Exception as e:
        logger.warning(f"Kokoro fallback failed for {speaker}: {e}")

    # No TTS engine available — return 204 so client shows text silently.
    return Response(
        content=b"", media_type="audio/mpeg", status_code=204, headers={"X-TTS-Engine": "none", "X-Speaker": speaker}
    )


@router.post("/api/stt")
async def api_stt(request: Request, audio: str = ""):
    """Transcribe uploaded audio using open-source Whisper (faster-whisper).

    Multipart form fields:
        file        — audio file (.mp3, .wav, .m4a, .ogg, etc.)
        language    — optional ISO-639-1 code (e.g. 'en', 'es')
        task        — 'transcribe' (default) or 'translate' (to English)

    Returns JSON:
        {
            "text": "full transcript",
            "language": "en",
            "language_probability": 0.98,
            "duration": 12.5,
            "segments": [...],
            "model": "large-v3-turbo",
            "elapsed_seconds": 1.23
        }
    """

    _bump("stt_requests")
    tenant = _tenant_id(request)

    form = await request.form()
    f = form.get("file")
    if not f or not hasattr(f, "read"):
        return JSONResponse({"error": "file field required"}, status_code=400)

    audio_bytes = await f.read()
    if not audio_bytes:
        return JSONResponse({"error": "empty audio file"}, status_code=400)

    language = (form.get("language") or "").strip() or None
    task = (form.get("task") or "transcribe").strip()

    try:
        from war_room.dashboard import whisper_stt as wt

        result = wt.transcribe(
            audio_bytes,
            language=language,
            task=task,
            vad_filter=True,
            word_timestamps=False,
        )
        result["tenant"] = tenant
        return JSONResponse(result)
    except Exception as e:
        logger.warning(f"Whisper STT failed: {e}")
        return JSONResponse({"error": "transcription failed", "detail": str(e)}, status_code=503)
