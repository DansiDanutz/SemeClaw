"""Adapter registry — single source of truth for ingest/writeback modules.

Every v1 adapter exposes the same shape: ``probe()``, ``ingest(limit)``,
``writeback(task, decision)``. This module gathers them so the admin SPA
+ ``semeclaw doctor`` can iterate without hard-coding names.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable

from war_room.v1 import discord_adapter as _discord
from war_room.v1 import jira_adapter as _jira
from war_room.v1 import linear_adapter as _linear
from war_room.v1 import notion_adapter as _notion

logger = logging.getLogger("semeclaw.v1.adapters")


class Adapter:
    __slots__ = ("id", "probe", "ingest", "writeback")

    def __init__(
        self,
        id: str,
        probe: Callable[[], dict],
        ingest: Callable[..., AsyncIterator[dict]],
        writeback: Callable[..., _AsyncDictResult],
    ) -> None:
        self.id = id
        self.probe = probe
        self.ingest = ingest
        self.writeback = writeback


# Imported lazily-friendly: each module is small and safe to import at startup.
REGISTRY: dict[str, Adapter] = {
    "discord": Adapter("discord", _discord.probe, _discord.ingest, _discord.writeback),
    "linear": Adapter("linear", _linear.probe, _linear.ingest, _linear.writeback),
    "notion": Adapter("notion", _notion.probe, _notion.ingest, _notion.writeback),
    "jira": Adapter("jira", _jira.probe, _jira.ingest, _jira.writeback),
}


def all_probes() -> list[dict]:
    return [a.probe() for a in REGISTRY.values()]


def get(adapter_id: str) -> Adapter | None:
    return REGISTRY.get(adapter_id)


# Mypy alias used in the Adapter ctor — the real return is `Awaitable[dict]`.
_AsyncDictResult = object
