"""Reports surface — list, read, create, upload, delete research reports.

Extracted from server.py (Phase 3.1 of the improvement goal, slice 2). Owns:

    GET    /api/reports          — list rolling + saved reports (retention pruned)
    GET    /api/reports/content  — full markdown of one report
    POST   /api/reports          — create from JSON (NERVIX/Paperclip ingest)
    POST   /api/reports/upload   — multipart .md upload
    DELETE /api/reports          — delete a report + cached meeting audio

Shared helpers that also serve the meeting surface (`_find_report`,
`_prune_old`, `_build_meeting_mp3`, `_safe_report_name`,
`_report_dir_for_tenant`, `_dispatch_webhook`, and the audio dirs) still
live in server.py and are imported lazily at call time — they migrate when
the meeting surface is extracted. server.py imports this module at startup,
so module-level imports back into it would be circular.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from war_room.dashboard.routes.deps import RESEARCH_DIR, _tenant_id

logger = logging.getLogger("war_room.dashboard.reports")
router = APIRouter(tags=["reports"])


def _srv():
    """The server module, resolved lazily (see module docstring)."""
    from war_room.dashboard import server

    return server


@router.get("/api/reports")
async def api_reports():
    srv = _srv()
    srv._prune_old()  # enforce retention on listing
    files = []
    for d, saved in ((srv.RESEARCH_SAVED, True), (RESEARCH_DIR, False)):
        for f in d.glob("*.md"):
            if not f.is_file():
                continue
            if d == RESEARCH_DIR and f.parent == srv.RESEARCH_SAVED:
                continue  # skip dir-ception
            files.append((f, saved))
    files.sort(key=lambda t: t[0].stat().st_mtime, reverse=True)

    reports = []
    for f, saved in files[:40]:
        reports.append(
            {
                "name": f.name,
                "saved": saved,
                "size": f.stat().st_size,
                "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                "preview": f.read_text(encoding="utf-8")[:300],
            }
        )
    return JSONResponse(reports)


@router.get("/api/reports/content")
async def api_report_content(name: str):
    """Return the full markdown content of a report (checks saved/ first, then rolling)."""
    srv = _srv()
    path = srv._find_report(name)
    if not path or path.suffix != ".md":
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        saved = path.parent == srv.RESEARCH_SAVED
        return JSONResponse({"name": path.name, "saved": saved, "content": path.read_text(encoding="utf-8")})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/reports")
async def api_reports_create(request: Request):
    """Create a new report from JSON. Called by external systems (NERVIX,
    Paperclip adapters) when they want SemeClaw to convene a meeting.

    Body:
        {
          "name":  optional safe filename — auto-generated from `task` if missing
          "task":  one-line subject
          "content": full markdown body (agents as ## sections recommended)
          "auto_audio": bool — if true, build the MP3 now
          "tags": optional list
        }
    Response:
        {name, url, audio_url, saved: false}
    """
    srv = _srv()
    data = await request.json()
    name = (data.get("name") or "").strip()
    task = (data.get("task") or "").strip()
    content = (data.get("content") or "").strip()

    if not content:
        return JSONResponse({"error": "content is required"}, status_code=400)
    if not name:
        # Auto-generate: "<slug>-YYYY-MM-DD.md"
        base = srv._safe_report_name(task).rstrip(".md")
        name = f"{base}-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.md"
    name = Path(srv._safe_report_name(name)).name

    # Ensure well-formed header
    if not content.lstrip().startswith("#"):
        header = (
            f"# War Room Report\n\n**Task:** {task or name}\n"
            f"**Date:** {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n**Via:** API\n\n---\n\n"
        )
        content = header + content

    path = srv._report_dir_for_tenant(request) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

    # Optional: generate the audio now
    audio_url = None
    if data.get("auto_audio"):
        mp3 = await srv._build_meeting_mp3(name)
        if mp3:
            audio_url = f"/api/meeting/audio?name={name}"

    url = f"/api/reports/content?name={name}"
    await srv._dispatch_webhook(
        "report.created",
        {
            "name": name,
            "task": task,
            "url": url,
            "audio_url": audio_url,
            "tenant_id": _tenant_id(request),
        },
    )
    return JSONResponse(
        {
            "name": name,
            "saved": False,
            "url": url,
            "audio_url": audio_url,
            "tenant_id": _tenant_id(request),
        },
        status_code=201,
    )


@router.post("/api/reports/upload")
async def api_reports_upload(request: Request):
    """Multipart upload — drop a .md file directly.

    Form fields:
        file:  the .md file
        task:  optional subject (defaults to file stem)
        auto_audio: "true"/"false"
    """
    srv = _srv()
    form = await request.form()
    f = form.get("file")
    if not f or not hasattr(f, "read"):
        return JSONResponse({"error": "file field required"}, status_code=400)
    raw = await f.read()
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        return JSONResponse({"error": "file must be utf-8 text"}, status_code=400)
    task = form.get("task") or Path(f.filename or "").stem or "task"
    name = srv._safe_report_name(Path(f.filename or "").stem or task)

    path = srv._report_dir_for_tenant(request) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

    audio_url = None
    if (form.get("auto_audio") or "").lower() in ("1", "true", "yes"):
        mp3 = await srv._build_meeting_mp3(name)
        if mp3:
            audio_url = f"/api/meeting/audio?name={name}"

    await srv._dispatch_webhook(
        "report.created",
        {
            "name": name,
            "task": task,
            "via": "upload",
            "url": f"/api/reports/content?name={name}",
            "audio_url": audio_url,
            "tenant_id": _tenant_id(request),
        },
    )
    return JSONResponse(
        {
            "name": name,
            "saved": False,
            "url": f"/api/reports/content?name={name}",
            "audio_url": audio_url,
            "tenant_id": _tenant_id(request),
        },
        status_code=201,
    )


@router.delete("/api/reports")
async def api_reports_delete(name: str):
    """Delete a report and its cached meeting audio (if any)."""
    srv = _srv()
    path = srv._find_report(name)
    if not path:
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        path.unlink()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    # Also remove any cached meeting MP3 whose stem matches this report
    removed_audio = 0
    for d in (srv.MEETINGS_DIR, srv.MEETINGS_SAVED):
        for f in d.glob("*.mp3"):
            if path.stem in f.stem:
                try:
                    f.unlink()
                    removed_audio += 1
                except Exception:
                    pass
    await srv._dispatch_webhook("report.deleted", {"name": name})
    return JSONResponse({"ok": True, "deleted": name, "audio_files_removed": removed_audio})
