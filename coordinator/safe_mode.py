"""
coordinator/safe_mode.py — Deterministic safe-mode stub.

When ALL backends in the fallback chain are OPEN (circuit-broken),
this stub activates. It never returns null — it queues the request
and returns a structured "degraded" response so callers can handle
gracefully instead of crashing.
"""
import logging
import time
from collections import deque

logger = logging.getLogger("coordinator.safe_mode")

_queue: deque[dict] = deque(maxlen=500)  # bounded work queue
_active = False


def activate():
    global _active
    if not _active:
        _active = True
        logger.critical("SAFE MODE ACTIVATED — all backends down, queueing work")


def deactivate():
    global _active
    if _active:
        _active = False
        logger.warning("Safe mode deactivated — backends recovering")


def is_active() -> bool:
    return _active


def queue_request(request_id: str, payload: dict) -> dict:
    """
    Queue a request for replay when backends recover.
    Returns a safe degraded response immediately.
    """
    entry = {
        "id": request_id,
        "payload": payload,
        "queued_at": time.time(),
    }
    _queue.append(entry)
    logger.warning("Queued request %s (queue depth: %d)", request_id, len(_queue))

    # Deterministic stub response — never null, never crashes caller
    return {
        "id": request_id,
        "object": "chat.completion",
        "model": "safe-mode-stub",
        "degraded": True,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": (
                        "⚠️ All AI backends are temporarily unavailable. "
                        f"Your request has been queued (position: {len(_queue)}) and will be "
                        "replayed automatically when a backend recovers. "
                        "Estimated wait: unknown. Contact Dan via Telegram if urgent."
                    ),
                },
                "finish_reason": "safe_mode",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def queue_depth() -> int:
    return len(_queue)


def drain_queue() -> list[dict]:
    """Pop all queued requests for replay. Called when a backend recovers."""
    items = list(_queue)
    _queue.clear()
    logger.info("Draining %d queued requests", len(items))
    return items
