"""DLQ replay handlers.

The existing /api/admin/dlq endpoints can list/peek/drain. v1 adds an
explicit replay loop: each DLQ kind gets a handler that knows how to
re-run the failed action. Failed re-attempts are written back to the
queue with an incremented `attempt` counter, capped at MAX_ATTEMPTS.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from war_room.v1 import storage as _s

logger = logging.getLogger("semeclaw.v1.dlq_replay")

MAX_ATTEMPTS = 3

ReplayHandler = Callable[[dict], Awaitable[bool]]
_HANDLERS: dict[str, ReplayHandler] = {}


def register(kind: str) -> Callable[[ReplayHandler], ReplayHandler]:
    """Decorator: register a handler for a given DLQ entry ``kind``."""

    def _wrap(fn: ReplayHandler) -> ReplayHandler:
        _HANDLERS[kind] = fn
        return fn

    return _wrap


# ---------------------------------------------------------------------------
# Default handlers — keep them safe and idempotent.
# ---------------------------------------------------------------------------
@register("tasks_post")
@register("tasks_patch")
async def _replay_tasks_supabase(entry: dict) -> bool:
    """Replay a Supabase task write through the existing _db.supa helper.

    Returns True if the call succeeds; False otherwise (the framework will
    re-queue with attempt+1).
    """
    try:
        from war_room.tasks import _db
    except Exception as exc:  # noqa: BLE001
        logger.warning("tasks _db not importable for replay: %s", exc)
        return False
    method = entry.get("kind", "").rsplit("_", 1)[-1]
    path = entry.get("path") or ""
    payload = entry.get("payload")
    if not method or not path:
        return False
    try:
        await _db.supa(method, path, json=payload)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.info("tasks replay failed: %s", exc)
        return False


@register("billing_usage")
async def _replay_billing_usage(entry: dict) -> bool:
    """Re-queue a billing usage record back through report_usage so the
    next flush picks it up. Returns True on a clean re-queue."""
    try:
        from war_room.v1 import billing
    except Exception as exc:  # noqa: BLE001
        logger.warning("billing module unavailable: %s", exc)
        return False
    if not billing.is_configured():
        return False
    tenant_id = entry.get("tenant_id")
    kind = entry.get("usage_kind") or entry.get("kind")
    qty = int(entry.get("quantity", 0) or 0)
    if not tenant_id or not kind or qty <= 0:
        return False
    await billing.report_usage(tenant_id, kind, quantity=qty)
    return True


@register("webhook_delivery")
async def _replay_webhook(entry: dict) -> bool:
    """Re-POST a webhook payload with the original signature header."""
    try:
        import httpx
    except Exception:
        return False
    url = entry.get("url")
    body = entry.get("body")
    headers = entry.get("headers") or {}
    if not url or body is None:
        return False
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(url, content=body, headers=headers)
            return r.status_code < 400
    except Exception as exc:  # noqa: BLE001
        logger.info("webhook replay failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Replay loop
# ---------------------------------------------------------------------------
async def replay_dlq(path: Path, dry_run: bool = False) -> dict:
    """Read every entry in the DLQ JSONL, attempt to replay it, and rewrite
    the file with only the entries that need another attempt.

    Concurrency: the read+rewrite is performed under the per-path lock
    used by ``append_jsonl`` so concurrent writers cannot have their
    entries silently overwritten. Any rows that landed between the
    snapshot read and the rewrite are preserved verbatim.

    Returns ``{replayed, failed, skipped, dropped, total}``.
    - replayed: handler reported success — entry removed
    - failed:   handler reported failure — entry re-queued with attempt+1
    - skipped:  no handler registered for this kind — entry kept as-is
    - dropped:  attempt counter exceeded MAX_ATTEMPTS — entry removed
    """
    if not path.exists():
        return {"replayed": 0, "failed": 0, "skipped": 0, "dropped": 0, "total": 0, "preserved": 0}

    counters = {"replayed": 0, "failed": 0, "skipped": 0, "dropped": 0, "total": 0, "preserved": 0}

    # Snapshot the current contents inside the lock to fix the set we will
    # operate on. Releasing the lock for the (potentially slow) handler
    # calls keeps writers unblocked; we re-acquire it before rewriting and
    # merge in anything that landed in the meantime.
    lock = _s._lock_for(path)
    async with lock:
        snapshot_bytes = path.read_bytes() if path.exists() else b""

    snapshot_entries: list[dict] = []
    for raw in snapshot_bytes.splitlines():
        if not raw.strip():
            continue
        try:
            snapshot_entries.append(json.loads(raw))
        except json.JSONDecodeError:
            continue

    keep: list[dict] = []
    for entry in snapshot_entries:
        counters["total"] += 1
        kind = entry.get("kind", "")
        handler = _HANDLERS.get(kind)
        attempt = int(entry.get("attempt", 0))
        if handler is None:
            counters["skipped"] += 1
            keep.append(entry)
            continue
        if attempt >= MAX_ATTEMPTS:
            counters["dropped"] += 1
            await _s.append_jsonl(
                _s.DLQ_REPLAY_LOG,
                {
                    "ts": _s.utcnow_iso(),
                    "kind": kind,
                    "outcome": "dropped",
                    "reason": f"attempt {attempt} exceeded cap {MAX_ATTEMPTS}",
                    "entry_path": entry.get("path"),
                },
            )
            continue
        if dry_run:
            counters["skipped"] += 1
            keep.append(entry)
            continue

        ok = False
        try:
            ok = bool(await handler(entry))
        except Exception as exc:  # noqa: BLE001
            logger.warning("replay handler raised for %s: %s", kind, exc)
            ok = False

        if ok:
            counters["replayed"] += 1
            await _s.append_jsonl(
                _s.DLQ_REPLAY_LOG,
                {"ts": _s.utcnow_iso(), "kind": kind, "outcome": "replayed", "attempt": attempt + 1},
            )
        else:
            counters["failed"] += 1
            keep.append({**entry, "attempt": attempt + 1, "last_attempt_at": _s.utcnow_iso()})

    if not dry_run:
        async with lock:
            # Read whatever is on disk NOW. Anything past the original
            # snapshot length is a concurrent append we must preserve.
            current_bytes = path.read_bytes() if path.exists() else b""
            new_tail = current_bytes[len(snapshot_bytes):]
            tail_entries: list[dict] = []
            for raw in new_tail.splitlines():
                if not raw.strip():
                    continue
                try:
                    tail_entries.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
            counters["preserved"] = len(tail_entries)

            tmp = path.with_suffix(path.suffix + ".replay.tmp")
            with tmp.open("w", encoding="utf-8") as f:
                for entry in keep:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                for entry in tail_entries:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            tmp.replace(path)
            if not keep and not tail_entries:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass

    return counters


def known_kinds() -> list[str]:
    return sorted(_HANDLERS.keys())


def replay_log_tail(limit: int = 50) -> list[dict]:
    entries = list(_s.iter_jsonl(_s.DLQ_REPLAY_LOG))
    return entries[-limit:][::-1]


# Make MAX_ATTEMPTS visible without importing the constant separately.
__all__ = [
    "register",
    "replay_dlq",
    "known_kinds",
    "replay_log_tail",
    "MAX_ATTEMPTS",
]


# Allow callers to format an audit-trail timestamp without re-importing datetime.
def _now() -> str:  # pragma: no cover - trivial
    return datetime.now(timezone.utc).isoformat()
