"""
Base adapter interface for external agent platforms.

Every adapter must implement:
  - health()         → AdapterHealth
  - sync_to_war_room() → list[dict]   (pull external state in)
  - sync_from_war_room(changes) → bool (push war room state out)
  - list_agents()    → list[dict]
  - list_tasks()     → list[dict]
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("war_room.adapters")


@dataclass
class AdapterHealth:
    name: str
    reachable: bool
    version: str | None = None
    error: str | None = None
    meta: dict[str, Any] | None = None


class Adapter(ABC):
    """Abstract base for platform adapters."""

    def __init__(self, name: str, base_url: str | None = None) -> None:
        self.name = name
        self.base_url = base_url
        self._logger = logging.getLogger(f"war_room.adapters.{name}")

    @abstractmethod
    async def health(self) -> AdapterHealth:
        """Return current health status."""

    @abstractmethod
    async def sync_to_war_room(self) -> list[dict[str, Any]]:
        """Pull external state into War Room format.

        Returns a list of normalized agent/task dicts.
        """

    @abstractmethod
    async def sync_from_war_room(self, changes: list[dict[str, Any]]) -> bool:
        """Push War Room changes out to the external platform.

        Returns True if all changes were accepted.
        """

    @abstractmethod
    async def list_agents(self) -> list[dict[str, Any]]:
        """Return available agents on the external platform."""

    @abstractmethod
    async def list_tasks(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        """Return tasks/issues from the external platform."""

    def _normalize_agent(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Override to map platform-specific agent shape to War Room shape."""
        return {
            "id": str(raw.get("id", "unknown")),
            "name": raw.get("name", "Unknown"),
            "role": raw.get("role", "Agent"),
            "status": raw.get("status", "idle"),
            "platform": self.name,
        }

    def _normalize_task(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Override to map platform-specific task shape to War Room shape."""
        return {
            "id": str(raw.get("id", "unknown")),
            "title": raw.get("title", "Untitled"),
            "status": raw.get("status", "open"),
            "assignee": raw.get("assignee"),
            "platform": self.name,
        }
