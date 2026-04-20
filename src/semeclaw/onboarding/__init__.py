"""SemeClaw onboarding — discover and sync local agents & tools."""

from semeclaw.onboarding.discovery import DiscoveredAgent, discover_all, to_json
from semeclaw.onboarding.sync import clear_discovered, sync_agents

__all__ = [
    "DiscoveredAgent",
    "discover_all",
    "to_json",
    "sync_agents",
    "clear_discovered",
]
