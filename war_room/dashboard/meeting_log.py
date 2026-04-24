"""Durable meeting-transcript log.

Every ``meeting_*`` WebSocket broadcast is persisted to
``data/meetings/{meeting_id}.jsonl`` with a monotonic ``seq`` field. Clients
that reconnect mid-meeting can call ``GET /api/meeting/{id}/transcript?since=N``
to fetch messages they missed.

The file format is JSON Lines (one object per line). Each line is the full
broadcast payload as it went out to WebSocket clients, so the backfill
response is semantically identical to having been online the whole time.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger("war_room.meeting_log")

# Default under data/ so Fly volume mount (/app/data) persists it.
DEFAULT_DIR = Path(os.environ.get("MEETING_LOG_DIR", "/app/data/meetings"))

# Message types that represent meeting events worth persisting.
_PERSISTABLE_TYPES = frozenset({
    "meeting_message",
    "meeting_task_start",
    "meeting_task_chunk",
    "meeting_task_user_question",
    "meeting_task_complete",
    "meeting_agent_turn",
    "meeting_slide",
})

# Max bytes per meeting file — safety valve against runaway growth.
_MAX_BYTES = int(os.environ.get("MEETING_LOG_MAX_BYTES", str(50 * 1024 * 1024)))  # 50 MiB


class MeetingLog:
    """Append-only per-meeting JSONL store with in-memory seq allocation.

    Crash behaviour: on startup ``_resume_seqs`` rebuilds seq counters from the
    last line of each existing file, so a restart doesn't reset numbering.
    """

    def __init__(self, dir_path: Path = DEFAULT_DIR) -> None:
        self._dir = dir_path
        self._seqs: dict[str, int] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    def _path(self, meeting_id: str) -> Path:
        # Very defensive: meeting_id is user-controlled somewhere upstream.
        safe = "".join(c for c in meeting_id if c.isalnum() or c in "-_") or "unknown"
        return self._dir / f"{safe}.jsonl"

    async def _lock_for(self, meeting_id: str) -> asyncio.Lock:
        async with self._global_lock:
            lock = self._locks.get(meeting_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[meeting_id] = lock
            return lock

    async def _init_seq(self, meeting_id: str) -> int:
        """Load the last seq from disk so restarts preserve monotonicity."""
        p = self._path(meeting_id)
        if not p.exists():
            return 0
        last_seq = 0
        try:
            # Read backwards to find the last non-empty line; small enough files
            # in practice that a full read is fine.
            for line in reversed(p.read_text(encoding="utf-8").splitlines()):
                if not line.strip():
                    continue
                try:
                    last_seq = int(json.loads(line).get("seq", 0))
                except Exception:  # noqa: BLE001
                    continue
                break
        except Exception as e:  # noqa: BLE001
            logger.warning("meeting_log resume failed for %s: %s", meeting_id, e)
        return last_seq

    async def record(self, message: dict) -> None:
        """Hook invoked before WS broadcast. Stamps ``seq`` and appends to disk.

        Mutates ``message`` in place by adding a ``seq`` field when the message
        is persistable. Non-meeting broadcasts are ignored.
        """
        if not isinstance(message, dict):
            return
        mtype = message.get("type", "")
        if mtype not in _PERSISTABLE_TYPES:
            return
        meeting_id = message.get("meeting_id")
        if not meeting_id:
            return

        lock = await self._lock_for(meeting_id)
        async with lock:
            if meeting_id not in self._seqs:
                self._seqs[meeting_id] = await self._init_seq(meeting_id)
            seq = self._seqs[meeting_id] + 1
            self._seqs[meeting_id] = seq
            message["seq"] = seq

            p = self._path(meeting_id)
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                # Safety valve — if the file has grown past the cap, rotate.
                if p.exists() and p.stat().st_size > _MAX_BYTES:
                    backup = p.with_suffix(".jsonl.prev")
                    try:
                        if backup.exists():
                            backup.unlink()
                        p.rename(backup)
                        logger.warning("meeting_log rotated %s (>%d bytes)", p, _MAX_BYTES)
                    except OSError:
                        pass
                with p.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(message, ensure_ascii=False) + "\n")
            except Exception as e:  # noqa: BLE001
                # Never break the broadcast path on disk failure.
                logger.error("meeting_log write failed for %s: %s", meeting_id, e)

    async def backfill(self, meeting_id: str, since: int = 0, limit: int = 2000) -> list[dict]:
        """Return persisted events for ``meeting_id`` with seq > ``since``.

        ``limit`` caps the response length so a buggy client can't DOS the
        server by asking for ``since=0`` on a long meeting.
        """
        p = self._path(meeting_id)
        if not p.exists():
            return []
        out: list[dict] = []
        try:
            with p.open("r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:  # noqa: BLE001
                        continue
                    if int(obj.get("seq", 0)) > since:
                        out.append(obj)
                        if len(out) >= limit:
                            break
        except Exception as e:  # noqa: BLE001
            logger.error("meeting_log backfill failed for %s: %s", meeting_id, e)
        return out


# Module-level singleton
meeting_log = MeetingLog()
