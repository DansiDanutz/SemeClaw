"""Mix background music into the silent demo video."""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent
DEMO = ROOT / "assets" / "demo"

VIDEO_IN = DEMO / "semeclaw_demo.mp4"
AUDIO_IN = DEMO / "meeting_background.wav"
VIDEO_TMP = DEMO / "semeclaw_demo_tmp.mp4"
VIDEO_OUT = DEMO / "semeclaw_demo.mp4"


def main() -> None:
    if not VIDEO_IN.exists():
        raise FileNotFoundError(f"Video not found: {VIDEO_IN}")
    if not AUDIO_IN.exists():
        raise FileNotFoundError(f"Audio not found: {AUDIO_IN}")

    # Video is ~15s, background is 32s — loop audio to cover video duration
    # Fade audio out at the end
    cmd = [
        "ffmpeg", "-y",
        "-i", str(VIDEO_IN),
        "-stream_loop", "-1", "-i", str(AUDIO_IN),
        "-shortest",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-af", "afade=t=out:st=12:d=3",
        "-movflags", "+faststart",
        str(VIDEO_TMP),
    ]
    print("Mixing audio into demo video...")
    subprocess.run(cmd, check=True)
    # Replace original
    VIDEO_TMP.replace(VIDEO_OUT)
    print(f"  [OK] {VIDEO_OUT}")


if __name__ == "__main__":
    main()
