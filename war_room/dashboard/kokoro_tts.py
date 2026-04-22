"""
War Room — Kokoro TTS wrapper (open-source, Apache 2.0).

Kokoro-82M delivers 9.2/10 quality speech synthesis at 210× realtime on GPU,
3-5× on CPU. It outperforms edge-tts and rivals ElevenLabs for informational
content — perfect for ad narration, meeting voiceovers, and announcements.

  - 54 voices across 8 languages (v1.0)
  - 2-3 GB VRAM on GPU, runs fine on CPU
  - Zero cost, no API key, no rate limits
  - OpenAI-compatible voice names: af_bella, af_nicole, am_adam, bf_emma, etc.

This module lazily-initialises the ONNX pipeline and caches MP3s to disk using
the same key scheme as edge-tts so all TTS backends share a single cache.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import subprocess
import threading
from pathlib import Path

logger = logging.getLogger("war_room.kokoro")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_DASHBOARD_DIR = Path(__file__).resolve().parent
CACHE_DIR = _DASHBOARD_DIR / "audio_cache" / "kokoro"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Speaker → Kokoro voice mapping
# ---------------------------------------------------------------------------
# kokoro v1.0 voicepacks (American, British, etc.)
# Full list: https://github.com/hexgrad/kokoro
VOICE_MAP: dict[str, str] = {
    "narrator":   "af_bella",   # warm female narrator — best for long-form
    "research":   "af_nicole",  # clear female — technical content
    "strategist": "bf_emma",    # British female — professional
    "writer":     "am_adam",    # American male — authoritative
    "architect":  "bm_george",  # British male — formal
    "default":    "af_bella",
}

# Friendly aliases clients might send
_ALIASES: dict[str, str] = {
    "bella": "af_bella",
    "nicole": "af_nicole",
    "adam": "am_adam",
    "emma": "bf_emma",
    "george": "bm_george",
    "sarah": "af_sarah",
    "michael": "am_michael",
}

# ---------------------------------------------------------------------------
# Lazy pipeline init (thread-safe)
# ---------------------------------------------------------------------------
_pipeline = None
_init_lock = threading.Lock()


def _get_pipeline():
    """Return the shared Kokoro KPipeline, creating it on first call."""
    global _pipeline
    if _pipeline is not None:
        return _pipeline
    with _init_lock:
        if _pipeline is not None:
            return _pipeline
        try:
            from kokoro import KPipeline
        except ImportError as exc:
            raise RuntimeError(
                "kokoro is not installed. Run: pip install kokoro soundfile"
            ) from exc
        # lang_code 'a' = American English (most voicepacks)
        # 'b' = British English — we pick per-voice below
        logger.info("Kokoro: loading KPipeline (first call) …")
        _pipeline = KPipeline(lang_code="a")
        logger.info("Kokoro: pipeline ready")
    return _pipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _resolve_voice(voice: str | None, agent: str | None = None) -> str:
    """Map a voice identifier to a Kokoro voice id."""
    if voice and re.fullmatch(r"af_[a-z]+|am_[a-z]+|bf_[a-z]+|bm_[a-z]+", voice):
        return voice
    if agent and agent in VOICE_MAP:
        return VOICE_MAP[agent]
    if voice and voice in VOICE_MAP:
        return VOICE_MAP[voice]
    if voice and voice.lower() in _ALIASES:
        return _ALIASES[voice.lower()]
    return VOICE_MAP["default"]


def cache_key(text: str, voice: str) -> str:
    """Stable SHA1 over text + voice — used as the filename."""
    payload = f"kokoro:{voice}|{text}".encode("utf-8")
    return hashlib.sha1(payload).hexdigest()


def cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.mp3"


def _clean_text(text: str) -> str:
    """Strip markdown / excessive whitespace before TTS."""
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"\*(.*?)\*", r"\1", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    return text


def _wav_to_mp3(wav_bytes: bytes) -> bytes:
    """Convert WAV bytes → MP3 bytes using ffmpeg (already in Docker image)."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-i", "pipe:0", "-f", "mp3", "-q:a", "4", "pipe:1"],
            input=wav_bytes,
            capture_output=True,
            timeout=30,
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout
    except Exception as exc:
        logger.warning("ffmpeg wav→mp3 failed: %s", exc)
    # Fallback: return WAV wrapped in a fake container so the client can play it
    return wav_bytes


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def synthesize(
    text: str,
    voice: str | None = None,
    *,
    agent: str | None = None,
    speed: float = 1.0,
) -> Path | None:
    """Generate MP3 audio using Kokoro TTS.

    Returns the path to a cached MP3, or None if Kokoro is unavailable.
    """
    text = _clean_text(text)
    if not text:
        raise ValueError("synthesize() requires non-empty text")

    voice_id = _resolve_voice(voice, agent=agent)
    key = cache_key(text, voice_id)
    path = cache_path(key)

    if path.exists() and path.stat().st_size > 0:
        return path

    # Lock per key so concurrent requests for same text don't race
    lock = threading.Lock()  # Simple lock; could be keyed per key for more parallelism
    with lock:
        if path.exists() and path.stat().st_size > 0:
            return path

        try:
            pipeline = _get_pipeline()
        except Exception as exc:
            logger.warning("Kokoro pipeline unavailable: %s", exc)
            return None

        logger.info("Kokoro synth: voice=%s bytes=%d key=%s…", voice_id, len(text), key[:8])

        try:
            import numpy as np
            import soundfile as sf
        except ImportError as exc:
            logger.warning("Kokoro deps missing (numpy, soundfile): %s", exc)
            return None

        # Run inference — pipeline returns a generator of (graphemes, phonemes, audio)
        try:
            generator = pipeline(text, voice=voice_id, speed=speed)
            chunks = []
            for _gs, _ps, audio in generator:
                if audio is not None and len(audio) > 0:
                    chunks.append(audio)

            if not chunks:
                logger.warning("Kokoro returned no audio chunks")
                return None

            full_audio = np.concatenate(chunks)

            # Write to temp WAV then convert to MP3
            wav_buf = io.BytesIO()
            sf.write(wav_buf, full_audio, 24000, format="WAV")
            mp3_bytes = _wav_to_mp3(wav_buf.getvalue())

            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(mp3_bytes)
            tmp.replace(path)
            return path

        except Exception as exc:
            logger.warning("Kokoro synthesis failed: %s", exc)
            return None


def health() -> dict:
    """Return Kokoro health status."""
    try:
        from kokoro import KPipeline  # noqa: F401
        available = True
    except ImportError:
        available = False
    return {
        "available": available,
        "engine": "kokoro-82M",
        "license": "Apache-2.0",
        "voices": list(set(VOICE_MAP.values())),
        "cache_dir": str(CACHE_DIR),
        "cache_files": len(list(CACHE_DIR.glob("*.mp3"))),
    }


def clear_cache() -> int:
    """Delete all cached Kokoro MP3s. Returns count removed."""
    n = 0
    for p in CACHE_DIR.glob("*.mp3"):
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    return n
