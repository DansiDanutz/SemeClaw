"""Server-sent events for live admin dashboard updates.

A single in-process pub/sub fanout. Producers call ``publish(event, data)``;
subscribers attach via ``stream()`` (an async generator wired into a
``StreamingResponse``).

Events are best-effort and bounded: a slow subscriber drops messages
silently once its bounded queue is full, so a stuck browser tab cannot
back-pressure the rest of the system.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

logger = logging.getLogger("semeclaw.v1.sse")

QUEUE_MAX = 100
KEEPALIVE_SECONDS = 20

_subscribers: set[asyncio.Queue] = set()


def subscriber_count() -> int:
    return len(_subscribers)


def publish(event: str, data: dict) -> None:
    """Fanout a payload to every active subscriber. Never raises."""
    if not _subscribers:
        return
    payload = (event, data)
    for q in list(_subscribers):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            # Subscriber is too slow — silently drop. They'll catch up on reconnect.
            logger.debug("dropping event for slow subscriber: %s", event)


async def stream() -> AsyncIterator[bytes]:
    """Yield server-sent-event encoded frames until the client disconnects."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
    _subscribers.add(queue)
    try:
        # Welcome event so the client sees an immediate signal.
        yield _frame("hello", {"subscribers": subscriber_count()})
        while True:
            try:
                event, data = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
                yield _frame(event, data)
            except asyncio.TimeoutError:
                # Comment-only keepalive — keeps proxies/browsers from closing the stream.
                yield b": keepalive\n\n"
    finally:
        _subscribers.discard(queue)


def _frame(event: str, data: dict) -> bytes:
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {body}\n\n".encode("utf-8")
