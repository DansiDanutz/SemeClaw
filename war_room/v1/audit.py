"""Audit log middleware + query helpers.

Every write request produces a single JSONL line with:
  {ts, request_id, tenant_id, actor, route, method, status,
   request_hash, response_hash, latency_ms}

Bodies are NEVER persisted — only their SHA-256 hashes — so the audit log
is safe to keep on disk even when payloads contain user content.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time

from war_room.v1 import storage as _s

logger = logging.getLogger("semeclaw.v1.audit")

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
EXCLUDED_PREFIXES = ("/metrics", "/api/tts", "/api/stt", "/ws")


def _hash(b: bytes | str | None) -> str:
    if b is None:
        return ""
    if isinstance(b, str):
        b = b.encode("utf-8", "replace")
    return hashlib.sha256(b).hexdigest()[:16]


def _route_key(path: str) -> str:
    """Cap path length so high-cardinality routes don't blow up the audit shard."""
    return path[:120]


async def _persist(entry: dict) -> None:
    try:
        await _s.append_audit(entry)
    except Exception as exc:  # noqa: BLE001
        # Audit writes must never break the request — log and move on.
        logger.warning("audit append failed: %s", exc)
    # Best-effort fanout to SSE subscribers so /admin can update live.
    try:
        from war_room.v1 import sse as _sse

        _sse.publish("audit", entry)
    except Exception:  # pragma: no cover - SSE is optional
        pass


def gc_audit(*, keep_days: int = 90) -> int:
    """Delete audit shards older than ``keep_days``. Returns the number removed."""
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    removed = 0
    for path in _s.list_audit_files():
        # filename format: YYYY-MM-DD.jsonl
        try:
            day = datetime.strptime(path.stem, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if day < cutoff:
            try:
                path.unlink()
                removed += 1
            except OSError as exc:  # noqa: BLE001
                logger.warning("audit gc failed for %s: %s", path, exc)
    return removed


def install(app) -> None:
    """Mount the audit middleware on a FastAPI app.

    Call this AFTER the existing _semeclaw_request_id middleware so the
    request_id contextvar is populated; FastAPI middlewares run in
    reverse-registration order, so registering this last makes it run
    closest to the handler.
    """

    @app.middleware("http")
    async def _audit_writes(request, call_next):
        if request.method not in WRITE_METHODS:
            return await call_next(request)
        path = request.url.path
        if any(path.startswith(p) for p in EXCLUDED_PREFIXES):
            return await call_next(request)

        # Snapshot request body for hashing without consuming it for the handler.
        try:
            body = await request.body()
        except Exception:
            body = b""

        async def _receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request._receive = _receive  # type: ignore[attr-defined]

        started = time.perf_counter()
        response = await call_next(request)
        latency_ms = int((time.perf_counter() - started) * 1000)

        # Tenant identity: trust the API-key-resolved value if present
        # (set by the tenant middleware), otherwise fall back to header.
        tenant_id = getattr(request.state, "tenant_id", None) or request.headers.get("x-tenant-id") or "default"
        actor = getattr(request.state, "actor", None) or "anonymous"

        entry = {
            "ts": _s.utcnow_iso(),
            "request_id": response.headers.get("X-Request-ID", ""),
            "tenant_id": tenant_id,
            "actor": actor,
            "route": _route_key(path),
            "method": request.method,
            "status": response.status_code,
            "request_hash": _hash(body),
            "latency_ms": latency_ms,
            "client_ip": (request.client.host if request.client else "") or "",
        }
        # Fire-and-forget so the response isn't held up by disk I/O.
        asyncio.create_task(_persist(entry))
        return response
