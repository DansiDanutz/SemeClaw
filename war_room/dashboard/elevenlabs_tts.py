"""
War Room — ElevenLabs TTS wrapper with automatic edge-tts fallback.

Strategy:
  1. If ELEVENLABS_API_KEY is set and the text looks like English,
     try ElevenLabs Flash v2.5 (streaming, low latency).
  2. On any failure (no key, rate limit, network, non-English),
     fall back to the local edge-tts module.
  3. Cache MP3s on disk using the same cache key scheme as edge-tts
     so the two backends share a single cache namespace.

Voice mapping:
  Each agent role gets a distinct ElevenLabs voice id. If the mapping
  doesn't contain the requested voice, we fall back to the default
  narrator voice.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import httpx

logger = logging.getLogger("war_room.elevenlabs")

ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_BASE = "https://api.elevenlabs.io/v1"

# Agent role → ElevenLabs voice id (Flash v2.5 compatible)
# These are the default premade voices; override via env if desired.
VOICE_MAP: dict[str, str] = {
    "narrator": os.environ.get("ELEVENLABS_VOICE_NARRATOR", "pNInz6obpgDQGcFmaJgB"),  # Adam
    "research": os.environ.get("ELEVENLABS_VOICE_RESEARCH", "yoZ06aMxZJJ28mfd3POQ"),  # Josh
    "strategist": os.environ.get("ELEVENLABS_VOICE_STRATEGIST", "XB0fDUnXU5powFXDhCwa"),  # Charlotte
    "writer": os.environ.get("ELEVENLABS_VOICE_WRITER", "TX3AE3VoEqMeqjLYdkPk"),  # Matthew
    "architect": os.environ.get("ELEVENLABS_VOICE_ARCHITECT", "IKne3meq5aSn9XLyUdCD"),  # Bill
}

DEFAULT_VOICE_ID = VOICE_MAP["narrator"]

# TTS model — Flash v2.5 is the fastest / cheapest tier
TTS_MODEL = "eleven_flash_v2_5"

# Voice settings: style=0.35 for expression per the GitHub spec
DEFAULT_SETTINGS = {
    "stability": 0.5,
    "similarity_boost": 0.75,
    "style": 0.35,
    "use_speaker_boost": True,
}


def _looks_like_english(text: str) -> bool:
    """Quick heuristic: if >20% non-ASCII, treat as non-English."""
    if not text:
        return False
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    return ascii_chars / len(text) >= 0.80


def _resolve_voice_id(voice: str | None, agent: str | None = None) -> str | None:
    """Map a voice identifier to an ElevenLabs voice id."""
    # If voice is already an ElevenLabs id (20-24 char alphanum), pass through
    if voice and re.fullmatch(r"[A-Za-z0-9]{20,24}", voice):
        return voice
    # Try agent role mapping
    if agent and agent in VOICE_MAP:
        return VOICE_MAP[agent]
    # Try voice as a role key
    if voice and voice in VOICE_MAP:
        return VOICE_MAP[voice]
    return DEFAULT_VOICE_ID


def _elevenlabs_key() -> str | None:
    key = ELEVENLABS_API_KEY
    return key if key else None


def _elevenlabs_api_key() -> str | None:
    return _elevenlabs_key()


def synthesize(
    text: str,
    voice: str | None = None,
    *,
    agent: str | None = None,
    rate: str = "+0%",
    pitch: str = "+0Hz",
    cache_dir: Path | None = None,
) -> Path | None:
    """Try ElevenLabs; return MP3 path on success, None if we should fall back.

    Args:
        text:  The text to synthesize.
        voice: Either an ElevenLabs voice id, an edge-tts voice name,
               or a role key like "narrator" / "research".
        agent: Agent role id — used to pick the right ElevenLabs voice.
        rate:  Ignored for ElevenLabs (no native prosody param).
        pitch: Ignored for ElevenLabs.
        cache_dir: Where to store cached MP3s. Defaults to tts.CACHE_DIR.
    """
    api_key = _elevenlabs_key()
    if not api_key:
        return None
    if not _looks_like_english(text):
        logger.debug("Non-English text detected — skipping ElevenLabs")
        return None

    voice_id = _resolve_voice_id(voice, agent=agent)
    if not voice_id:
        return None

    # Import tts late to avoid circular deps and sandbox issues
    try:
        import tts as tts_mod
    except ImportError:
        return None

    if cache_dir is None:
        cache_dir = tts_mod.CACHE_DIR

    # Use the same cache key scheme so edge-tts and ElevenLabs share cache
    # We bake the voice_id into the key so switching providers invalidates cleanly
    key = tts_mod.cache_key(text, f"elevenlabs:{voice_id}", rate=rate, pitch=pitch)
    path = cache_dir / f"{key}.mp3"
    if path.exists() and path.stat().st_size > 0:
        return path

    logger.info("ElevenLabs synth: voice_id=%s bytes=%d key=%s...", voice_id, len(text), key[:8])

    payload = {
        "text": text,
        "model_id": TTS_MODEL,
        "voice_settings": DEFAULT_SETTINGS,
    }

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{ELEVENLABS_BASE}/text-to-speech/{voice_id}/stream",
                headers={
                    "xi-api-key": api_key,
                    "Content-Type": "application/json",
                    "Accept": "audio/mpeg",
                },
                json=payload,
            )
            resp.raise_for_status()
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(resp.content)
            tmp.replace(path)
            return path
    except httpx.HTTPStatusError as e:
        logger.warning("ElevenLabs HTTP %s — falling back to edge-tts: %s", e.response.status_code, e)
        return None
    except Exception as e:
        logger.warning("ElevenLabs failed — falling back to edge-tts: %s", e)
        return None


def health() -> dict:
    """Return ElevenLabs health status for the dashboard."""
    api_key = _elevenlabs_api_key()
    if not api_key:
        return {"available": False, "reason": "ELEVENLABS_API_KEY not set"}
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.get(
                f"{ELEVENLABS_BASE}/user/subscription",
                headers={"xi-api-key": api_key},
            )
            r.raise_for_status()
            data = r.json()
            return {
                "available": True,
                "tier": data.get("tier", "unknown"),
                "character_limit": data.get("character_limit"),
                "character_count": data.get("character_count"),
            }
    except Exception as e:
        return {"available": False, "reason": str(e)}
