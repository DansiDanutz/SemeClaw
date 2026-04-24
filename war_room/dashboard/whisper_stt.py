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
# Model quality vs. speed tradeoffs (faster-whisper):
#   tiny            39M   ~8x realtime   — demo only
#   base            74M   ~5x realtime   — previously the default; too noisy for production meetings
#   small          244M   ~2x realtime   — production-grade CPU default
#   medium         769M   ~1x realtime   — high quality, heavier CPU
#   large-v3      1.5B    ~0.5x rt       — best quality, GPU recommended
#   large-v3-turbo 809M   ~3x rt on GPU  — best quality/speed on GPU
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cpu").strip() or "cpu"  # "cpu" or "cuda"

# Default chosen so a fresh fly deploy sounds good:
#   GPU available   -> large-v3-turbo (best on GPU)
#   CPU only        -> small          (production-usable on a Fly shared CPU)
_DEFAULT_MODEL = "large-v3-turbo" if WHISPER_DEVICE == "cuda" else "small"
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", _DEFAULT_MODEL).strip() or _DEFAULT_MODEL

# Compute type: int8 is fastest but lossy; float16/float32 preserve quality.
#   CPU -> int8 is the pragmatic default (still small model).
#   GPU -> float16 is the right balance on modern hardware.
_DEFAULT_COMPUTE = "float16" if WHISPER_DEVICE == "cuda" else "int8"
WHISPER_COMPUTE = os.environ.get("WHISPER_COMPUTE", _DEFAULT_COMPUTE).strip() or _DEFAULT_COMPUTE

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
            raise RuntimeError("faster-whisper is not installed. Run: pip install faster-whisper") from exc
        logger.info(
            "Whisper: loading model='%s' device='%s' compute='%s' …",
            WHISPER_MODEL,
            WHISPER_DEVICE,
            WHISPER_COMPUTE,
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
                "ffmpeg",
                "-i",
                "pipe:0",
                "-ar",
                "16000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                "-f",
                "wav",
                "pipe:1",
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
            info.language,
            info.language_probability,
            len(segments),
            info.duration,
            elapsed,
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
