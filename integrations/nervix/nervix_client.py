"""
Nervix Client — SemeClaw ↔ Nervix Agent Society bridge.

Allows SemeClaw to:
  - Send messages to any Nervix agent
  - Create war rooms for critical incidents
  - Query society state (agents, tasks, economy)
  - Receive callbacks when meetings finalize

Usage:
    from integrations.nervix.nervix_client import NervixClient
    client = NervixClient()
    await client.chat("dexter", "We need a latency audit on API v2")
    room = await client.create_war_room("Production API Down", priority="P0")
"""
import json
import os
from typing import Dict, List, Optional
import httpx

NERVIX_RUNTIME_URL = os.environ.get("NERVIX_RUNTIME_URL", "http://127.0.0.1:8000")
DEFAULT_TIMEOUT = 30.0


class NervixClient:
    """HTTP client for the Nervix Agent Runtime API."""

    def __init__(self, base_url: str = NERVIX_RUNTIME_URL, timeout: float = DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=timeout)

    # ── Agent Communication ──

    async def chat(self, agent_id: str, message: str, chat_id: str = "semeclaw") -> Dict:
        """Send a message to a Nervix agent and get its response."""
        payload = {
            "agent_id": agent_id,
            "message": message,
            "chat_id": chat_id,
            "user_id": "semeclaw",
        }
        resp = await self.client.post(f"{self.base_url}/chat", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def send_to_bus(self, from_agent: str, to_agent: str, content: str, message_type: str = "dm") -> Dict:
        """Send a direct message via the Nervix message bus."""
        payload = {
            "from_agent": from_agent,
            "to_agent": to_agent,
            "content": content,
            "message_type": message_type,
        }
        resp = await self.client.post(f"{self.base_url}/agent-message", json=payload)
        resp.raise_for_status()
        return resp.json()

    # ── War Rooms ──

    async def create_war_room(
        self,
        topic: str,
        priority: str = "P1",
        participants: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
    ) -> Dict:
        """Create a Nervix war room. Returns the room dict."""
        payload = {
            "topic": topic,
            "priority": priority,
            "participants": participants or ["semeclaw"],
            "tags": tags or [],
        }
        resp = await self.client.post(f"{self.base_url}/war-rooms", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def send_war_room_message(self, room_id: str, content: str, from_agent: str = "semeclaw") -> Dict:
        """Send a message to an active war room."""
        payload = {
            "from_agent": from_agent,
            "content": content,
        }
        resp = await self.client.post(f"{self.base_url}/war-rooms/{room_id}/messages", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def resolve_war_room(self, room_id: str, summary: str = "") -> Dict:
        """Resolve and archive a war room."""
        payload = {"summary": summary}
        resp = await self.client.post(f"{self.base_url}/war-rooms/{room_id}/resolve", json=payload)
        resp.raise_for_status()
        return resp.json()

    # ── Society ──

    async def get_snapshot(self) -> Dict:
        """Get full society snapshot (population, economy, tasks, observations)."""
        resp = await self.client.get(f"{self.base_url}/society/snapshot")
        resp.raise_for_status()
        return resp.json()

    async def get_agents(self) -> List[Dict]:
        """List all agents and their status."""
        resp = await self.client.get(f"{self.base_url}/agents/status")
        resp.raise_for_status()
        return resp.json().get("agents", [])

    async def create_task(self, title: str, description: str, reward: int = 10, priority: str = "P2", tags: Optional[List[str]] = None) -> Dict:
        """Create a bounty task for agents."""
        payload = {
            "title": title,
            "description": description,
            "reward": reward,
            "priority": priority,
            "tags": tags or [],
        }
        resp = await self.client.post(f"{self.base_url}/society/tasks", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def get_tasks(self, limit: int = 10) -> List[Dict]:
        """Get recent society tasks."""
        resp = await self.client.get(f"{self.base_url}/society/tasks", params={"limit": limit})
        resp.raise_for_status()
        return resp.json().get("tasks", [])

    # ── Health ──

    async def health(self) -> Dict:
        """Check Nervix runtime health."""
        resp = await self.client.get(f"{self.base_url}/health")
        resp.raise_for_status()
        return resp.json()

    async def close(self):
        await self.client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()
