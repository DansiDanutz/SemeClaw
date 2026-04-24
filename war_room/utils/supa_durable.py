"""Durable Supabase REST helper — retry with backoff + optional dead-letter.

Shared between ``adclaw.server`` (impression logging) and ``war_room.tasks``
(task subsystem). Both had the same silent-swallow-on-error failure mode
before this module existed.

Usage:
    from war_room.utils.supa_durable import with_retry, dlq_append

    # transient-retrying wrapper around a supa()-shaped callable
    result = await with_retry(my_supa, "post", "path", json=body)

    # append a failed operation to a JSONL dead-letter so an operator /
    # cron can replay it when Supabase is healthy again
    dlq_append(dlq_path, {"kind": "task_upsert", "payload": body, "error": str(exc)})
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger("war_room.utils.supa_durable")

DEFAULT_MAX_ATTEMPTS = int(os.environ.get("SEMECLAW_SUPA_RETRIES", "3"))
_BACKOFF_BASE_S = 0.1
_BACKOFF_CAP_S = 2.0


async def with_retry(
    fn: Callable[..., Awaitable[Any]],
    *args: Any,
    attempts: int = DEFAULT_MAX_ATTEMPTS,
    **kwargs: Any,
) -> Any:
    """Call ``fn(*args, **kwargs)`` with bounded exponential backoff.

    Re-raises the last exception if all attempts fail. ``fn`` is typically a
    ``supa``-like coroutine: ``supa(method, path, json=...)``.
    """
    last: Exception | None = None
    for i in range(attempts):
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — retry any error
            last = exc
            if i == attempts - 1:
                break
            delay = min(_BACKOFF_CAP_S, _BACKOFF_BASE_S * (2**i))
            await asyncio.sleep(delay)
    assert last is not None
    raise last


def dlq_append(path: Path | str, entry: dict) -> None:
    """Append one JSON object per line to ``path``. Best-effort.

    Never raises — if the DLQ path is unwritable we log and move on, because
    throwing here would mask the original write failure that got us to the
    dead-letter in the first place.
    """
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.error("dlq_append failed for %s: %s; entry dropped: %s", p, exc, entry)
