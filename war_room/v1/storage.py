"""Local file-backed storage for v1 features.

Rationale: SemeClaw's primary persistence is Supabase, but v1 features
must work locally for the demo and zero-key open-source flow. This module
gives every v1 feature a JSONL/JSON store under ``data/v1/`` that the
admin dashboard can read directly.

Thread-safety: an asyncio Lock per file path serialises writes. Reads are
unsynchronised (best-effort consistency, fine for an admin UI).
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

def _data_dir() -> Path:
    """Resolve the v1 data dir on every call so tests can override
    ``SEMECLAW_V1_DATA_DIR`` without reloading the module."""
    p = Path(os.environ.get("SEMECLAW_V1_DATA_DIR", "data/v1")).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    (p / "audit").mkdir(parents=True, exist_ok=True)
    return p


class _PathProxy:
    """Resolves a path relative to the *current* V1_DATA_DIR on every use."""

    __slots__ = ("_rel",)

    def __init__(self, rel: str) -> None:
        self._rel = rel

    def _resolve(self) -> Path:
        return _data_dir() / self._rel

    # Allow proxy to behave like a Path for the operations we use.
    def __fspath__(self) -> str:  # PEP 519
        return str(self._resolve())

    def __getattr__(self, name: str):  # delegate to the underlying Path
        return getattr(self._resolve(), name)

    def __truediv__(self, other):
        return self._resolve() / other

    def __str__(self) -> str:
        return str(self._resolve())

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"_PathProxy({self._rel!r} -> {self._resolve()})"


# Public module attributes — re-resolve when accessed via the proxy.
V1_DATA_DIR = _PathProxy("")
TENANTS_FILE = _PathProxy("tenants.json")
AUDIT_DIR = _PathProxy("audit")
DLQ_REPLAY_LOG = _PathProxy("dlq_replay.jsonl")
USAGE_FILE = _PathProxy("usage.json")

# Use a plain dict so we never construct an asyncio.Lock at module-import
# time (the lock binds to whatever event loop is running at first access,
# which causes deadlocks across pytest-asyncio loops).
_LOCKS: dict[str, asyncio.Lock] = {}


def _lock_for(path: Path) -> asyncio.Lock:
    key = str(Path(os.fspath(path)).resolve())
    lock = _LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[key] = lock
    return lock


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# JSON document store (single-file documents like tenants.json)
# ---------------------------------------------------------------------------
async def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _write_json_unlocked(path: Path, data: Any) -> None:
    """Atomic file write — caller MUST hold the per-path lock when racing
    with other writers. Used by callers that already own the lock to
    avoid the non-reentrant ``asyncio.Lock`` deadlock."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


async def write_json(path: Path, data: Any) -> None:
    async with _lock_for(path):
        _write_json_unlocked(path, data)


# ---------------------------------------------------------------------------
# JSONL append-only store (audit log, replay log)
# ---------------------------------------------------------------------------
async def append_jsonl(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, separators=(",", ":"), ensure_ascii=False) + "\n"
    async with _lock_for(path):
        with path.open("a", encoding="utf-8") as f:
            f.write(line)


def iter_jsonl(path: Path) -> Iterable[dict]:
    """Synchronous iteration for read endpoints — keeps the admin query simple."""
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


# ---------------------------------------------------------------------------
# Audit log helpers — sharded by date so files stay manageable.
# ---------------------------------------------------------------------------
def _audit_path_for(when: datetime) -> Path:
    return AUDIT_DIR / f"{when.strftime('%Y-%m-%d')}.jsonl"


async def append_audit(entry: dict) -> None:
    await append_jsonl(_audit_path_for(datetime.now(timezone.utc)), entry)


def list_audit_files() -> list[Path]:
    return sorted(AUDIT_DIR.glob("*.jsonl"))


def query_audit(
    *,
    tenant_id: str | None = None,
    route_prefix: str | None = None,
    method: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Best-effort filter across all daily shards. Newest first.

    Date filters are ISO timestamps; partial dates ("2026-04-24") work.

    Files are walked newest→oldest; within each file entries are read
    chronologically and then reversed before extending the result, so
    the final list is consistently newest-first even when the limit
    straddles a shard boundary.
    """
    method_norm = method.upper() if method else None
    out: list[dict] = []
    for path in reversed(list_audit_files()):
        per_file: list[dict] = []
        for entry in iter_jsonl(path):
            ts = entry.get("ts", "")
            if since and ts < since:
                continue
            if until and ts > until:
                continue
            if tenant_id and entry.get("tenant_id") != tenant_id:
                continue
            if method_norm and entry.get("method") != method_norm:
                continue
            if route_prefix and not (entry.get("route") or "").startswith(route_prefix):
                continue
            per_file.append(entry)
        # Within a shard, entries were appended chronologically — reverse
        # so the newest line of this file precedes the oldest.
        per_file.reverse()
        out.extend(per_file)
        if len(out) >= limit:
            return out[:limit]
    return out[:limit]
