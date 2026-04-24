"""
War Room — Text-to-speech helper (edge-tts wrapper with disk cache).

Why edge-tts?
  - Free, no API key.
  - High-quality neural voices (the same Microsoft uses in Edge's "Read aloud").
  - Supports per-voice prosody so we can hand a different voice to each agent.

Why cache?
  - Each scenario re-plays the same lines. Generating audio once per
    (text, voice) saves seconds of latency and avoids re-hitting Microsoft.
  - MP3s are written to   <war_room>/dashboard/audio_cache/<sha1>.mp3

Sandbox note:
  - This module runs on the user's machine inside the Flask server process.
  - The sandbox where Claude executes cannot reach speech.platform.bing.com
    (no DNS). That's fine — we only import edge-tts lazily so `import tts`
    never fails just because the library isn't installed.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import threading
from pathlib import Path

logger = logging.getLogger("war_room.tts")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_DASHBOARD_DIR = Path(__file__).resolve().parent
CACHE_DIR = _DASHBOARD_DIR / "audio_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Default voice if a caller forgets to specify one
DEFAULT_VOICE = "en-US-SteffanNeural"

# A single in-process lock so concurrent /api/tts requests don't both try to
# write the same file at the same time.
_write_locks: dict[str, threading.Lock] = {}
_locks_mutex = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _locks_mutex:
        lock = _write_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _write_locks[key] = lock
        return lock


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def cache_key(text: str, voice: str, *, rate: str = "+0%", pitch: str = "+0Hz") -> str:
    """Stable SHA1 over text + voice + prosody — used as the filename."""
    payload = f"{voice}|{rate}|{pitch}|{text}".encode()
    return hashlib.sha1(payload).hexdigest()


def cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.mp3"


def _clean_text(text: str) -> str:
    """Strip markdown / excessive whitespace before handing to TTS."""
    if not text:
        return ""
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    # Strip simple markdown emphasis
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"\*(.*?)\*", r"\1", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    return text


async def _synthesize_async(
    text: str,
    voice: str,
    out_path: Path,
    *,
    rate: str = "+0%",
    pitch: str = "+0Hz",
) -> None:
    """Call edge-tts and write an MP3 to `out_path`."""
    try:
        import edge_tts  # type: ignore
    except ImportError as e:
        raise RuntimeError("edge-tts is not installed. On the user's machine run: pip install edge-tts") from e

    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    # Write to a temp sibling then rename atomically
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    try:
        await communicate.save(str(tmp))
        tmp.replace(out_path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def synthesize(
    text: str,
    voice: str = DEFAULT_VOICE,
    *,
    rate: str = "+0%",
    pitch: str = "+0Hz",
    agent: str | None = None,
) -> Path:
    """Unified TTS — tries ElevenLabs first, falls back to edge-tts.

    Args:
        text:  The utterance to synthesize.
        voice: Edge-tts voice id (used as fallback key and label).
        rate:  Edge-tts prosody rate.
        pitch: Edge-tts prosody pitch.
        agent: Agent role id — used to pick the ElevenLabs voice.

    Returns:
        Path to the cached MP3.
    """
    text = _clean_text(text)
    if not text:
        raise ValueError("synthesize() requires non-empty text")

    # --- Try ElevenLabs first (premium, fast) ---
    try:
        import elevenlabs_tts as el

        el_path = el.synthesize(text, voice=voice, agent=agent, rate=rate, pitch=pitch)
        if el_path is not None:
            return el_path
    except Exception:
        logger.debug("ElevenLabs unavailable, falling back to edge-tts")

    # --- Fall back to edge-tts (free, offline) ---
    key = cache_key(text, voice, rate=rate, pitch=pitch)
    path = cache_path(key)
    if path.exists() and path.stat().st_size > 0:
        return path

    lock = _lock_for(key)
    with lock:
        if path.exists() and path.stat().st_size > 0:
            return path
        logger.info("TTS synth: voice=%s bytes=%d key=%s...", voice, len(text), key[:8])
        edge_err: Exception | None = None
        try:
            try:
                asyncio.run(_synthesize_async(text, voice, path, rate=rate, pitch=pitch))
            except RuntimeError as e:
                if "event loop" in str(e).lower():
                    result: dict[str, Exception | None] = {"err": None}

                    def _worker() -> None:
                        try:
                            asyncio.run(_synthesize_async(text, voice, path, rate=rate, pitch=pitch))
                        except Exception as inner:
                            result["err"] = inner

                    t = threading.Thread(target=_worker, daemon=True)
                    t.start()
                    t.join()
                    if result["err"] is not None:
                        raise result["err"]
                else:
                    raise
        except Exception as e:  # noqa: BLE001 — any failure triggers kokoro fallback
            edge_err = e
            logger.warning("edge-tts failed, trying kokoro: %s", e)

    # If edge-tts produced a file we're done.
    if path.exists() and path.stat().st_size > 0:
        return path

    # --- Tier 3: Kokoro ONNX (local, offline, no API key) ------------------
    try:
        from war_room.dashboard import kokoro_tts as kt

        kokoro_path = kt.synthesize(text, voice=voice, agent=agent)
        if kokoro_path is not None and kokoro_path.exists() and kokoro_path.stat().st_size > 0:
            logger.info("TTS served by kokoro fallback for key=%s", key[:8])
            return kokoro_path
    except Exception as e:  # noqa: BLE001
        logger.error("kokoro fallback also failed: %s (after edge err: %s)", e, edge_err)

    # Re-raise the original edge-tts error if we got here with nothing.
    if edge_err is not None:
        raise edge_err
    raise RuntimeError(f"TTS failed: no engine produced audio for voice={voice}")


def clear_cache() -> int:
    """Delete all cached MP3s. Returns the count removed."""
    n = 0
    for p in CACHE_DIR.glob("*.mp3"):
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    return n
