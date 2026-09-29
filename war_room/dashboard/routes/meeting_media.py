"""Meeting media surface — script building and the persistent meeting-audio cache.

Extracted from server.py (Phase 3.1 of the improvement goal, slice 5). Owns:

    GET  /api/meeting/script  — report → playable meeting script (translated when lang != en)
    GET  /api/meeting/audio   — cached concatenated meeting MP3 (built on first call)
    GET  /api/meeting/list    — list cached meeting MP3s (prunes >48h unpinned first)
    POST /api/meeting/pin     — pin a meeting MP3 + its report past the 48h window
    POST /api/meeting/unpin   — move them back to the rolling window

It is also the home of the shared meeting/report retention helpers
(`_find_report`, `_prune_old`, `_build_meeting_mp3`, the audio dirs), which
previously lived in server.py and are re-exported from there for the callers
that remain in the monolith. server.py imports this module at startup, so
imports back into it happen lazily at call time via `_srv()`.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from war_room.dashboard.routes.deps import RESEARCH_DIR, STATE_FILE, WAR_ROOM_DIR

logger = logging.getLogger("war_room.dashboard.meeting_media")
router = APIRouter(tags=["meeting-media"])


def _srv():
    """The server module, resolved lazily (see module docstring)."""
    from war_room.dashboard import server

    return server


# ---------------------------------------------------------------------------
# Persistent audio cache for meetings
# ---------------------------------------------------------------------------

AUDIO_DIR = WAR_ROOM_DIR / "audio"
MEETINGS_DIR = AUDIO_DIR / "meetings"  # rolling — 48h retention
MEETINGS_SAVED = AUDIO_DIR / "meetings" / "saved"  # pinned — kept forever
SEGMENTS_DIR = AUDIO_DIR / "segments"
for d in (AUDIO_DIR, MEETINGS_DIR, MEETINGS_SAVED, SEGMENTS_DIR):
    d.mkdir(parents=True, exist_ok=True)

MEETING_RETENTION_HOURS = 48
REPORT_RETENTION_HOURS = 48

RESEARCH_SAVED = RESEARCH_DIR / "saved"
RESEARCH_SAVED.mkdir(parents=True, exist_ok=True)


def _find_report(name: str) -> Path | None:
    """Find a report by filename, checking saved/ first, then rolling."""
    safe = Path(name).name
    for d in (RESEARCH_SAVED, RESEARCH_DIR):
        p = d / safe
        if p.exists() and p.is_file():
            return p
    return None


def _find_cached_meeting(stem_prefix: str) -> Path | None:
    """Look for a cached meeting file (saved first, then rolling)."""
    for d in (MEETINGS_SAVED, MEETINGS_DIR):
        for f in d.glob(f"{stem_prefix}*.mp3"):
            return f
    return None


def _prune_old() -> dict[str, int]:
    """Delete unpinned meeting MP3s and report MDs older than their retention window."""
    import time

    now = time.time()
    out = {"meetings": 0, "reports": 0}

    # Meetings (48h)
    m_cutoff = now - MEETING_RETENTION_HOURS * 3600
    for f in MEETINGS_DIR.glob("*.mp3"):
        if f.parent == MEETINGS_SAVED:
            continue
        try:
            if f.stat().st_mtime < m_cutoff:
                f.unlink()
                out["meetings"] += 1
        except Exception:
            pass

    # Reports (48h) — rolling only, skip saved/
    r_cutoff = now - REPORT_RETENTION_HOURS * 3600
    for f in RESEARCH_DIR.glob("*.md"):
        if f.parent == RESEARCH_SAVED:
            continue
        try:
            if f.stat().st_mtime < r_cutoff:
                f.unlink()
                out["reports"] += 1
        except Exception:
            pass

    # 100-task cap on completed_tasks in shared_state.json
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            tasks = state.get("completed_tasks", [])
            if len(tasks) > 100:
                tasks_sorted = sorted(
                    tasks,
                    key=lambda t: t.get("completed_at", ""),
                    reverse=True,
                )
                kept = tasks_sorted[:100]
                removed = tasks_sorted[100:]
                # Delete report files for evicted tasks
                for evicted in removed:
                    rname = evicted.get("report_name") or evicted.get("report")
                    if rname:
                        rpath = RESEARCH_DIR / rname
                        try:
                            if rpath.exists() and rpath.parent != RESEARCH_SAVED:
                                rpath.unlink()
                                out["reports"] += 1
                        except Exception:
                            pass
                state["completed_tasks"] = kept
                STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("_prune_old 100-task cap error: %s", e)

    return out


# Backward-compat alias (old call sites)
def _prune_old_meetings() -> int:
    return _prune_old()["meetings"]


def _meeting_cache_path(meeting_id: str, safe_name: str) -> Path:
    stem = re.sub(r"[^a-zA-Z0-9_-]", "_", safe_name.removesuffix(".md"))[:80]
    return MEETINGS_DIR / f"{meeting_id}_{stem}.mp3"


async def _synthesize_segment(client: httpx.AsyncClient, speaker: str, text: str) -> bytes | None:
    """Call our own /api/tts via localhost to leverage the existing ElevenLabs/edge-tts pipeline."""
    try:
        resp = await client.get(
            "http://127.0.0.1:8765/api/tts",
            params={"text": text, "speaker": speaker, "lang": "en"},
            timeout=30.0,
        )
        if resp.status_code == 200 and resp.content:
            return resp.content
    except Exception as e:
        logger.warning(f"segment synth failed for {speaker}: {e}")
    return None


async def _build_meeting_mp3(name: str) -> Path | None:
    """Generate + cache the concatenated meeting MP3 for a report. Returns cached path."""
    import re as _re
    import shutil
    import subprocess
    import tempfile

    from meeting_skill import build_script

    srv = _srv()
    report_path = _find_report(name)
    if not report_path:
        return None
    safe_name = report_path.name
    content = report_path.read_text(encoding="utf-8")
    task = srv._extract_task_from_report(content)
    meeting_id = srv._lookup_run_id_for_task(task) or _re.sub(r"\W", "", safe_name)[:8] or "000"
    script = build_script(report_content=content, task=task, meeting_id=meeting_id)

    cache_path = _meeting_cache_path(script.meeting_id, safe_name)
    if cache_path.exists() and cache_path.stat().st_size > 2048:
        return cache_path

    ffmpeg = shutil.which("ffmpeg") or next(
        (p for p in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg") if Path(p).exists()),
        None,
    )
    if not ffmpeg:
        logger.warning("ffmpeg not found — cannot build meeting MP3 cache")
        return None

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        list_lines: list[str] = []

        async with httpx.AsyncClient() as client:
            for i, seg in enumerate(script.segments):
                audio = await _synthesize_segment(client, seg.speaker, seg.text)
                if not audio:
                    continue
                seg_path = tmp_dir / f"{i:02d}_{seg.speaker}.mp3"
                seg_path.write_bytes(audio)
                list_lines.append(f"file '{seg_path}'")

                pause_ms = max(0, min(1500, seg.pause_ms_after))
                if pause_ms:
                    sil = tmp_dir / f"sil_{pause_ms}.mp3"
                    if not sil.exists():
                        subprocess.run(
                            [
                                ffmpeg,
                                "-y",
                                "-f",
                                "lavfi",
                                "-i",
                                "anullsrc=r=44100:cl=stereo",
                                "-t",
                                f"{pause_ms / 1000:.2f}",
                                "-q:a",
                                "2",
                                str(sil),
                            ],
                            capture_output=True,
                        )
                    list_lines.append(f"file '{sil}'")

        if not list_lines:
            return None

        list_path = tmp_dir / "concat.txt"
        list_path.write_text("\n".join(list_lines))
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_path),
                "-c:a",
                "libmp3lame",
                "-q:a",
                "2",
                str(cache_path),
            ],
            capture_output=True,
        )

    if not cache_path.exists() or cache_path.stat().st_size < 2048:
        return None
    return cache_path


def _move_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        src.rename(dest)
    except OSError:
        dest.write_bytes(src.read_bytes())
        src.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/api/meeting/script")
async def api_meeting_script(name: str, lang: str = "en", request: Request = None):
    """Convert a report into a playable meeting script. Translates when lang != en.

    Free tier: enforces FREE_TIER_WAIT_SECONDS server-side delay regardless of client.
    Pro tier:  no delay — provide X-SemeClaw-License header with a valid pro key.
    """
    import asyncio

    from meeting_skill import build_script

    srv = _srv()
    path = _find_report(name)
    if not path or path.suffix != ".md":
        return JSONResponse({"error": "not found"}, status_code=404)
    content = path.read_text(encoding="utf-8")
    task = srv._extract_task_from_report(content)
    run_id = srv._lookup_run_id_for_task(task) or path.stem

    script = build_script(report_content=content, task=task, meeting_id=run_id)
    payload = script.to_dict()

    if lang and lang != "en":
        cache_stem = f"{payload['meeting_id']}_{lang}.json"
        cache_path = srv.SCRIPTS_CACHE_DIR / cache_stem
        if cache_path.exists():
            try:
                return JSONResponse(json.loads(cache_path.read_text(encoding="utf-8")))
            except Exception:
                cache_path.unlink(missing_ok=True)
        payload["segments"] = await srv._translate_script(payload["segments"], lang)
        payload["lang"] = lang
        try:
            cache_path.write_text(json.dumps(payload), encoding="utf-8")
        except Exception:
            pass
    else:
        payload["lang"] = "en"

    # Free-tier gate: server-enforced wait so the loading screen can't be skipped
    # even by calling this endpoint directly (e.g. from a custom client or curl).
    if request and srv._get_tier(request) == "free" and srv.FREE_TIER_WAIT_SECONDS > 0:
        await asyncio.sleep(srv.FREE_TIER_WAIT_SECONDS)

    return JSONResponse(payload)


@router.get("/api/meeting/audio")
async def api_meeting_audio(name: str, download: bool = False):
    """Return the cached meeting MP3 for a report (generated on first call)."""
    path = await _build_meeting_mp3(name)
    if not path:
        return JSONResponse({"error": "could not build meeting audio"}, status_code=500)
    headers = {"Cache-Control": "public, max-age=86400"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{path.name}"'
    return FileResponse(path, media_type="audio/mpeg", headers=headers)


@router.get("/api/meeting/list")
async def api_meeting_list():
    """List all cached meeting MP3s (rolling + saved). Prunes unsaved ones >48h first."""
    pruned = _prune_old()
    items = []
    for d, saved in ((MEETINGS_SAVED, True), (MEETINGS_DIR, False)):
        for f in sorted(d.glob("*.mp3"), key=lambda x: x.stat().st_mtime, reverse=True):
            if f.parent.name == "saved" and not saved:
                continue
            items.append(
                {
                    "file": f.name,
                    "saved": saved,
                    "size_kb": round(f.stat().st_size / 1024, 1),
                    "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                }
            )
    return JSONResponse(
        {
            "items": items,
            "pruned_this_call": pruned,
            "retention_hours": {"meetings": MEETING_RETENTION_HOURS, "reports": REPORT_RETENTION_HOURS},
        }
    )


@router.post("/api/meeting/pin")
async def api_meeting_pin(name: str):
    """Pin the REPORT + its cached meeting MP3. Both survive 48h cleanup."""
    # 1. Build (or find) the meeting audio
    audio_path = await _build_meeting_mp3(name)
    if not audio_path:
        return JSONResponse({"error": "could not build meeting audio"}, status_code=500)
    if audio_path.parent != MEETINGS_SAVED:
        _move_file(audio_path, MEETINGS_SAVED / audio_path.name)

    # 2. Pin the underlying report .md too
    report_path = _find_report(name)
    if report_path and report_path.parent != RESEARCH_SAVED:
        _move_file(report_path, RESEARCH_SAVED / report_path.name)

    return JSONResponse(
        {
            "ok": True,
            "audio_file": audio_path.name,
            "report_file": Path(name).name,
            "saved": True,
        }
    )


@router.post("/api/meeting/unpin")
async def api_meeting_unpin(name: str = "", file: str = ""):
    """Unpin a meeting + its report. Accepts either the report name or the audio filename."""
    moved = []
    # Report side
    report_name = Path(name).name if name else ""
    if report_name:
        src = RESEARCH_SAVED / report_name
        if src.exists():
            _move_file(src, RESEARCH_DIR / report_name)
            moved.append(report_name)
    # Audio side
    if file:
        src = MEETINGS_SAVED / Path(file).name
        if src.exists():
            _move_file(src, MEETINGS_DIR / Path(file).name)
            moved.append(Path(file).name)
    elif report_name:
        # Try to locate the audio by filename pattern
        for f in MEETINGS_SAVED.glob("*.mp3"):
            if report_name.removesuffix(".md").lower() in f.name.lower():
                _move_file(f, MEETINGS_DIR / f.name)
                moved.append(f.name)
                break
    if not moved:
        return JSONResponse({"error": "nothing to unpin"}, status_code=404)
    return JSONResponse({"ok": True, "moved": moved, "saved": False})
