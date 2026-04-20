"""
SemeClaw Onboarding — Sync discovered agents into the War Room dashboard.

Writes:
  1. war_room/shared_state.json → discovered_agents list
  2. war_room/agents/*.md       → markdown stubs for new external agents
  3. war_room/dashboard/index.html is NOT touched; the frontend reads
     /api/agents and /api/board which now include discovered data.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .discovery import DiscoveredAgent, to_json

logger = logging.getLogger("semeclaw.onboarding.sync")

WAR_ROOM_DIR = Path("war_room")
STATE_FILE = WAR_ROOM_DIR / "shared_state.json"
AGENTS_DIR = WAR_ROOM_DIR / "agents"


def _load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_state(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _agent_markdown(agent: DiscoveredAgent) -> str:
    """Generate a minimal markdown agent definition."""
    lines = [
        "---",
        f"name: {agent.name}",
        f"id: {agent.id}",
        f"kind: {agent.kind}",
        f"status: {agent.status}",
    ]
    if agent.version:
        lines.append(f"version: {agent.version}")
    if agent.path:
        lines.append(f"path: {agent.path}")
    if agent.url:
        lines.append(f"url: {agent.url}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {agent.name}")
    lines.append("")
    lines.append(f"Discovered via onboarding ({agent.meta.get('source', 'unknown')}).")
    if agent.meta.get("manifest"):
        lines.append(f"Manifest: `{agent.meta['manifest']}`")
    lines.append("")
    return "\n".join(lines)


def sync_agents(agents: list[DiscoveredAgent], dry_run: bool = False) -> dict[str, Any]:
    """Sync discovered agents into the War Room state and agents directory.

    Returns:
        A summary dict with counts and lists of created/updated agents.
    """
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)

    state = _load_state()
    previously = {a["id"] for a in state.get("discovered_agents", [])}

    created: list[str] = []
    updated: list[str] = []
    skipped: list[str] = []

    for agent in agents:
        # 1. Write/update agent markdown stub
        md_path = AGENTS_DIR / f"{agent.id}.md"
        content = _agent_markdown(agent)

        if md_path.exists():
            existing = md_path.read_text(encoding="utf-8")
            if existing != content:
                if not dry_run:
                    md_path.write_text(content, encoding="utf-8")
                updated.append(agent.id)
            else:
                skipped.append(agent.id)
        else:
            if not dry_run:
                md_path.write_text(content, encoding="utf-8")
            created.append(agent.id)

    # 2. Update shared state
    state["discovered_agents"] = to_json(agents)
    state["onboarding_last_run"] = _now_iso()

    if not dry_run:
        _save_state(state)

    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "total": len(agents),
        "previously_known": len(previously),
    }


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def clear_discovered() -> None:
    """Remove all discovered agents from state and agents dir."""
    state = _load_state()
    state.pop("discovered_agents", None)
    state.pop("onboarding_last_run", None)
    _save_state(state)

    for md in AGENTS_DIR.glob("*.md"):
        text = md.read_text(encoding="utf-8")
        if "Discovered via onboarding" in text:
            md.unlink()
            logger.info("Removed discovered agent stub: %s", md.name)
