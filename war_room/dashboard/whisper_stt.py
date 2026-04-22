"""
War Room — Whisper STT wrapper (open-source, MIT license).

Uses faster-whisper for efficient CPU/GPU transcription.
  - large-v3-turbo: 809M params, 216× realtime, 99+ languages, 6GB VRAM
  - distilled: 756M params, 6× faster than large-v3, English-only
  - tiny: 39M params, runs on Raspberry Pi

Model is downloaded on first use (~1-3 GB) and kept in:
  ~/.cache/huggingface/hub/models--Systran--faster-whisper-*/

This module is designed for batch transcription (advertiser voice memos,
meeting recordings, etc.) — not real-time streaming.
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import BinaryIO

logger = logging.getLogger("war_room.whisper")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# Model size: tiny, base, small, medium, large-v1, large-v2, large-v3, large-v3-turbo, distil-*
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "large-v3-turbo").strip() or "large-v3-turbo"
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cpu").strip() or "cpu"  # "cpu" or "cuda"
WHISPER_COMPUTE = os.environ.get("WHISPER_COMPUTE", "int8").strip() or "int8"  # int8, float16, float32

# ---------------------------------------------------------------------------
# Lazy model init (thread-safe)
# ---------------------------------------------------------------------------
_model = None
_init_lock = threading.Lock()


def _get_model():
    """Return the shared WhisperModel, downloading on first call."""
    global _model
    if _model is not None:
        return _model
    with _init_lock:
        if _model is not None:
            return _model
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper is not installed. Run: pip install faster-whisper"
            ) from exc
        logger.info(
            "Whisper: loading model='%s' device='%s' compute='%s' …",
            WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE,
        )
        t0 = time.time()
        _model = WhisperModel(
            WHISPER_MODEL,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE,
            download_root=None,  # uses ~/.cache
        )
        logger.info("Whisper: model ready in %.1fs", time.time() - t0)
    return _model


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _ensure_wav(input_bytes: bytes) -> bytes:
    """Convert any audio ffmpeg understands → 16 kHz mono WAV."""
    if not isinstance(input_bytes, bytes):
        raise TypeError("_ensure_wav requires bytes input")
    try:
        import subprocess as sp
        proc = sp.run(
            [
                "ffmpeg", "-i", "pipe:0",
                "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
                "-f", "wav", "pipe:1",
            ],
            input=input_bytes,
            capture_output=True,
            timeout=60,
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout
        logger.warning("ffmpeg normalize failed: %s", proc.stderr.decode()[:200])
    except Exception as exc:
        logger.warning("ffmpeg normalize error: %s", exc)
    # If ffmpeg fails, assume input is already WAV and hope for the best
    return input_bytes


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def transcribe(
    audio: bytes | BinaryIO | Path,
    *,
    language: str | None = None,
    task: str = "transcribe",  # "transcribe" or "translate"
    vad_filter: bool = True,
    word_timestamps: bool = False,
) -> dict:
    """Transcribe audio to text.

    Args:
        audio: Raw audio bytes, file-like object, or Path.
        language: ISO-639-1 code (e.g. 'en', 'es'). Auto-detected if None.
        task: "transcribe" or "translate" (to English).
        vad_filter: Remove silence / non-speech segments.
        word_timestamps: Include per-word timing.

    Returns:
        {
            "text": "full transcript",
            "language": "en",
            "language_probability": 0.98,
            "duration": 12.5,
            "segments": [
                {
                    "id": 0,
                    "start": 0.0,
                    "end": 3.2,
                    "text": "Hello world",
                    "words": [...]  # if word_timestamps=True
                }
            ]
        }
    """
    model = _get_model()

    # Convert input to a temporary file (faster-whisper prefers file paths)
    if isinstance(audio, (str, Path)):
        audio_path = Path(audio)
        cleanup = False
    else:
        if hasattr(audio, "read"):
            raw = audio.read()
        else:
            raw = audio
        raw = _ensure_wav(raw)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(raw)
            audio_path = Path(tmp.name)
        cleanup = True

    try:
        t0 = time.time()
        segments_iter, info = model.transcribe(
            str(audio_path),
            language=language,
            task=task,
            vad_filter=vad_filter,
            word_timestamps=word_timestamps,
        )

        segments = []
        for seg in segments_iter:
            seg_dict = {
                "id": seg.id,
                "start": round(seg.start, 2),
                "end": round(seg.end, 2),
                "text": seg.text.strip(),
            }
            if word_timestamps and seg.words:
                seg_dict["words"] = [
                    {
                        "start": round(w.start, 2),
                        "end": round(w.end, 2),
                        "word": w.word.strip(),
                        "probability": round(w.probability, 3),
                    }
                    for w in seg.words
                ]
            segments.append(seg_dict)

        full_text = " ".join(s["text"] for s in segments).strip()
        elapsed = round(time.time() - t0, 2)

        logger.info(
            "Whisper transcribe: lang=%s prob=%.2f segments=%d dur=%.1fs elapsed=%.2fs",
            info.language, info.language_probability, len(segments),
            info.duration, elapsed,
        )

        return {
            "text": full_text,
            "language": info.language,
            "language_probability": round(info.language_probability, 3),
            "duration": round(info.duration, 2),
            "segments": segments,
            "model": WHISPER_MODEL,
            "elapsed_seconds": elapsed,
        }

    finally:
        if cleanup and audio_path.exists():
            try:
                audio_path.unlink()
            except OSError:
                pass


def health() -> dict:
    """Return Whisper health status."""
    try:
        from faster_whisper import WhisperModel  # noqa: F401
        available = True
    except ImportError:
        available = False
    return {
        "available": available,
        "engine": "faster-whisper",
        "model": WHISPER_MODEL,
        "device": WHISPER_DEVICE,
        "compute_type": WHISPER_COMPUTE,
        "license": "MIT",
    }
