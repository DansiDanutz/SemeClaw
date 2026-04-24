"""Windows audio playback for meeting room ambiance and brand sounds.

Uses winmm.dll via ctypes — no extra dependencies on Windows.
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

SND_ASYNC = 0x0001
SND_LOOP = 0x0008
SND_FILENAME = 0x0002
SND_PURGE = 0x0040
SND_NODEFAULT = 0x0002

_winmm = ctypes.windll.winmm if sys.platform == "win32" else None


def _play(path: Path, flags: int = 0) -> None:
    if _winmm is None:
        return
    # PlaySoundW takes LPCWSTR
    _winmm.PlaySoundW(str(path), None, flags | SND_FILENAME | SND_NODEFAULT)


def play_background(wav_path: Path) -> None:
    """Start looping background music (non-blocking)."""
    _play(wav_path, SND_ASYNC | SND_LOOP)


def play_jingle(wav_path: Path) -> None:
    """Play a one-shot sound (non-blocking)."""
    _play(wav_path, SND_ASYNC)


def stop_all() -> None:
    """Stop all playing sounds."""
    if _winmm is None:
        return
    _winmm.PlaySoundW(None, None, SND_PURGE)
