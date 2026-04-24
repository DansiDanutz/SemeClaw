"""Process-wide shared ``httpx.AsyncClient`` pool.

Rationale: per-request ``async with httpx.AsyncClient(...):`` opens a new TLS
connection every time. A shared client reuses connections via HTTP/1.1
keep-alive (and HTTP/2 multiplexing if the remote supports it), typically
cutting per-call latency by 20-80 ms to Supabase and similar hosts.

Call ``await close_shared_clients()`` on app shutdown.
"""

from __future__ import annotations

import asyncio

import httpx

# Keyed by base_url so different upstreams don\'t share a connection pool.
_CLIENTS: dict[str, httpx.AsyncClient] = {}
_LOCK = asyncio.Lock()


async def get_shared_client(
    base_url: str = "",
    *,
    timeout: float = 15.0,
    headers: dict | None = None,
) -> httpx.AsyncClient:
    """Return (and lazily create) the shared client for ``base_url``.

    Tests can pass a different base_url / timeout and get an isolated client.
    The long-lived clients are closed via ``close_shared_clients``.
    """
    key = base_url or "__default__"
    async with _LOCK:
        client = _CLIENTS.get(key)
        if client is None or client.is_closed:
            client = httpx.AsyncClient(
                base_url=base_url,
                timeout=timeout,
                headers=headers or {},
                # Use a generous connection pool so bursts don\'t queue.
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            )
            _CLIENTS[key] = client
        return client


async def close_shared_clients() -> None:
    """Best-effort close every client. Idempotent."""
    async with _LOCK:
        for client in list(_CLIENTS.values()):
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass
        _CLIENTS.clear()
