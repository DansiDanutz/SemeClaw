"""
War Room — Kokoro TTS wrapper (open-source, Apache 2.0).

Uses kokoro-onnx for lightweight ONNX inference — no torch, no spacy,
no compilation needed. Perfect for Docker/Fly.io deployment.

  - 5 voices: af_bella, af_nicole, am_adam, bf_emma, bm_george
  - ~80MB quantized model
  - Near real-time on CPU
  - Zero cost, no API key

Model files (auto-downloaded on first use):
  - kokoro-v1.0.onnx    (~300MB, or ~80MB int8 quantized)
  - voices-v1.0.bin     (voice embeddings)
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

# Model files — downloaded on first use if not present
MODEL_DIR = _DASHBOARD_DIR / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Speaker → Kokoro voice mapping
# ---------------------------------------------------------------------------
VOICE_MAP: dict[str, str] = {
    "narrator":   "af_bella",
    "research":   "af_nicole",
    "strategist": "bf_emma",
    "writer":     "am_adam",
    "architect":  "bm_george",
    "default":    "af_bella",
}

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
# Lazy model init (thread-safe)
# ---------------------------------------------------------------------------
_kokoro = None
_init_lock = threading.Lock()


def _model_path() -> Path:
    """Return path to ONNX model, downloading if needed."""
    p = MODEL_DIR / "kokoro-v1.0.onnx"
    if p.exists():
        return p
    # Try int8 quantized (smaller)
    p_int8 = MODEL_DIR / "kokoro-v1.0.int8.onnx"
    if p_int8.exists():
        return p_int8
    return p


def _voices_path() -> Path:
    return MODEL_DIR / "voices-v1.0.bin"


def _download_models():
    """Download Kokoro ONNX model + voices if missing."""
    model = _model_path()
    voices = _voices_path()

    if voices.exists() and model.exists():
        return

    logger.info("Kokoro: downloading model files …")
    urls = []
    if not voices.exists():
        urls.append((
            "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
            voices,
        ))
    if not model.exists():
        # Prefer int8 quantized (smaller, faster)
        urls.append((
            "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx",
            MODEL_DIR / "kokoro-v1.0.int8.onnx",
        ))

    for url, dest in urls:
        try:
            import urllib.request
            logger.info("Kokoro: downloading %s → %s", url, dest.name)
            urllib.request.urlretrieve(url, dest)
        except Exception as exc:
            logger.warning("Kokoro: failed to download %s: %s", url, exc)


def _get_kokoro():
    """Return the shared Kokoro instance, creating it on first call."""
    global _kokoro
    if _kokoro is not None:
        return _kokoro
    with _init_lock:
        if _kokoro is not None:
            return _kokoro
        try:
            from kokoro_onnx import Kokoro
        except ImportError as exc:
            raise RuntimeError(
                "kokoro-onnx is not installed. Run: pip install kokoro-onnx soundfile"
            ) from exc

        _download_models()
        model = _model_path()
        voices = _voices_path()

        if not model.exists():
            raise RuntimeError(f"Kokoro model not found: {model}")
        if not voices.exists():
            raise RuntimeError(f"Kokoro voices not found: {voices}")

        logger.info("Kokoro: loading model=%s voices=%s", model.name, voices.name)
        _kokoro = Kokoro(str(model), str(voices))
        logger.info("Kokoro: ready")
    return _kokoro


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _resolve_voice(voice: str | None, agent: str | None = None) -> str:
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
    payload = f"kokoro:{voice}|{text}".encode("utf-8")
    return hashlib.sha1(payload).hexdigest()


def cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.mp3"


def _clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"\*(.*?)\*", r"\1", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    return text


def _wav_to_mp3(wav_bytes: bytes) -> bytes:
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
    """Generate MP3 audio using Kokoro ONNX TTS. Returns cached MP3 path or None."""
    text = _clean_text(text)
    if not text:
        raise ValueError("synthesize() requires non-empty text")

    voice_id = _resolve_voice(voice, agent=agent)
    key = cache_key(text, voice_id)
    path = cache_path(key)

    if path.exists() and path.stat().st_size > 0:
        return path

    lock = threading.Lock()
    with lock:
        if path.exists() and path.stat().st_size > 0:
            return path

        try:
            kokoro = _get_kokoro()
        except Exception as exc:
            logger.warning("Kokoro unavailable: %s", exc)
            return None

        logger.info("Kokoro synth: voice=%s bytes=%d key=%s…", voice_id, len(text), key[:8])

        try:
            samples, sample_rate = kokoro.create(
                text, voice=voice_id, speed=speed, lang="en-us"
            )
            if samples is None or len(samples) == 0:
                logger.warning("Kokoro returned no audio")
                return None

            import numpy as np
            import soundfile as sf

            wav_buf = io.BytesIO()
            sf.write(wav_buf, samples, sample_rate, format="WAV")
            mp3_bytes = _wav_to_mp3(wav_buf.getvalue())

            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(mp3_bytes)
            tmp.replace(path)
            return path

        except Exception as exc:
            logger.warning("Kokoro synthesis failed: %s", exc)
            return None


def health() -> dict:
    try:
        from kokoro_onnx import Kokoro  # noqa: F401
        available = True
    except ImportError:
        available = False
    model_exists = _model_path().exists() and _voices_path().exists()
    return {
        "available": available,
        "model_downloaded": model_exists,
        "engine": "kokoro-onnx",
        "license": "Apache-2.0",
        "voices": list(set(VOICE_MAP.values())),
        "cache_files": len(list(CACHE_DIR.glob("*.mp3"))),
    }


def clear_cache() -> int:
    n = 0
    for p in CACHE_DIR.glob("*.mp3"):
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    return n
