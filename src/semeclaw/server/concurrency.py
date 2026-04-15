"""Concurrency control for agent workers."""

from __future__ import annotations

import asyncio


class AgentSemaphorePool:
    """Manages per-agent concurrency limits using semaphores."""

    def __init__(self) -> None:
        """Initialize the semaphore pool."""
        self._semaphores: dict[str, asyncio.Semaphore] = {}

    def get(self, agent_id: str, max_concurrency: int = 1) -> asyncio.Semaphore:
        """Get or create a semaphore for an agent.

        Args:
            agent_id: The agent identifier
            max_concurrency: Maximum concurrent sessions for this agent (default 1)

        Returns:
            asyncio.Semaphore for this agent
        """
        if agent_id not in self._semaphores:
            self._semaphores[agent_id] = asyncio.Semaphore(max_concurrency)
        return self._semaphores[agent_id]

    def cleanup(self, agent_id: str) -> None:
        """Clean up a semaphore if no waiters remain.

        Args:
            agent_id: The agent identifier
        """
        if agent_id in self._semaphores and not self._semaphores[agent_id]._waiters:
            del self._semaphores[agent_id]
