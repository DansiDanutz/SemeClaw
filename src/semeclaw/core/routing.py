"""Routing table for mapping event sources to agents."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from semeclaw.core.events import EventSource


@dataclass
class Binding:
    """A binding between a source pattern and an agent."""

    agent_id: str
    pattern: str | None = None  # Regex pattern to match source
    tier: int = 0  # Priority tier (higher = higher priority)

    def matches(self, source_str: str) -> bool:
        """Check if this binding matches a source.

        Args:
            source_str: String representation of the source.

        Returns:
            True if the pattern matches.
        """
        if self.pattern is None:
            return True
        return re.match(self.pattern, source_str) is not None


class RoutingTable:
    """Maps event sources to agent IDs."""

    def __init__(self):
        """Initialize the routing table."""
        self.bindings: list[Binding] = []
        self.logger = logging.getLogger(__name__)

    def add_binding(self, agent_id: str, pattern: str | None = None, tier: int = 0) -> None:
        """Add a binding to the routing table.

        Args:
            agent_id: The target agent ID.
            pattern: Optional regex pattern to match sources.
            tier: Priority tier (higher = higher priority).
        """
        binding = Binding(agent_id=agent_id, pattern=pattern, tier=tier)
        self.bindings.append(binding)
        # Sort by tier (descending) and then by pattern specificity
        self.bindings.sort(key=lambda b: (-b.tier, b.pattern is not None), reverse=False)
        self.logger.debug(f"Added binding: {agent_id} -> {pattern or '*'} (tier={tier})")

    def resolve(self, source: EventSource) -> str | None:
        """Resolve a source to an agent ID.

        Args:
            source: The event source.

        Returns:
            The agent ID, or None if no matching binding found.
        """
        source_str = str(source)

        for binding in self.bindings:
            if binding.matches(source_str):
                self.logger.debug(f"Resolved {source_str} to {binding.agent_id}")
                return binding.agent_id

        self.logger.warning(f"No binding found for source: {source_str}")
        return None

    def clear(self) -> None:
        """Clear all bindings."""
        self.bindings.clear()
        self.logger.debug("Routing table cleared")

    def __repr__(self) -> str:
        """Get string representation."""
        return f"RoutingTable({len(self.bindings)} bindings)"
