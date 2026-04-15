"""Multi-layer prompt builder for SemeClaw agents."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from semeclaw.core.events import EventSource, CronEventSource, AgentEventSource

if TYPE_CHECKING:
    from semeclaw.core.session_state import SessionState


class PromptBuilder:
    """Assembles system prompt from 5 layers: identity, personality, bootstrap, runtime, channel."""

    def __init__(self, workspace_path: Path):
        """Initialize PromptBuilder.

        Args:
            workspace_path: Path to the workspace root (contains agents/, crons/, etc.)
        """
        self.workspace_path = Path(workspace_path)

    def build(self, state: "SessionState", source: EventSource | None = None) -> str:
        """Build complete system prompt from all layers.

        Args:
            state: Current session state with agent definition
            source: Event source (cron, agent, platform, etc.) for channel hints

        Returns:
            Complete system prompt string
        """
        layers = []

        # Layer 1: Identity (AGENT.md)
        identity = self._build_identity(state.agent.agent_def.agent_md)
        layers.append(identity)

        # Layer 2: Personality (SOUL.md if exists)
        personality = self._build_personality(state.agent.agent_def.id)
        if personality:
            layers.append(personality)

        # Layer 3: Bootstrap context
        bootstrap = self._load_bootstrap_context()
        if bootstrap:
            layers.append(bootstrap)

        # Layer 4: Memory context (what Seme already knows)
        memory_ctx = self._build_memory_context()
        if memory_ctx:
            layers.append(memory_ctx)

        # Layer 5: Runtime context
        runtime = self._build_runtime_context(
            state.agent.agent_def.id, time.time()
        )
        if runtime:
            layers.append(runtime)

        # Layer 6: Channel hint
        if source:
            channel = self._build_channel_hint(source)
            if channel:
                layers.append(channel)

        return "\n\n".join(layers)

    def _build_identity(self, agent_md: str) -> str:
        """Extract and format identity layer from AGENT.md content.

        Args:
            agent_md: Raw AGENT.md content

        Returns:
            Formatted identity prompt
        """
        return agent_md

    def _build_personality(self, agent_id: str) -> str | None:
        """Load personality layer from SOUL.md if it exists.

        Args:
            agent_id: Agent identifier

        Returns:
            Personality prompt or None if SOUL.md doesn't exist
        """
        soul_path = self.workspace_path / "agents" / agent_id / "SOUL.md"
        if not soul_path.exists():
            return None

        try:
            content = soul_path.read_text()
            # Extract just the body content (skip frontmatter if present)
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    return parts[2].strip()
            return content.strip()
        except Exception:
            return None

    def _load_bootstrap_context(self) -> str | None:
        """Load bootstrap context from BOOTSTRAP.md and AGENTS.md.

        Returns:
            Bootstrap context or None
        """
        layers = []

        # Load BOOTSTRAP.md if exists
        bootstrap_path = self.workspace_path / "BOOTSTRAP.md"
        if bootstrap_path.exists():
            try:
                content = bootstrap_path.read_text()
                # Skip frontmatter
                if content.startswith("---"):
                    parts = content.split("---", 2)
                    if len(parts) >= 3:
                        content = parts[2].strip()
                layers.append(content)
            except Exception:
                pass

        # Load AGENTS.md if exists
        agents_path = self.workspace_path / "AGENTS.md"
        if agents_path.exists():
            try:
                content = agents_path.read_text()
                # Skip frontmatter
                if content.startswith("---"):
                    parts = content.split("---", 2)
                    if len(parts) >= 3:
                        content = parts[2].strip()
                layers.append(content)
            except Exception:
                pass

        # Add cron list
        cron_list = self._format_cron_list()
        if cron_list:
            layers.append(cron_list)

        return "\n\n".join(layers) if layers else None

    def _format_cron_list(self) -> str | None:
        """Format list of available cron jobs.

        Returns:
            Formatted cron list or None if no crons exist
        """
        crons_path = self.workspace_path / "crons"
        if not crons_path.exists():
            return None

        cron_ids = []
        try:
            for cron_dir in crons_path.iterdir():
                if cron_dir.is_dir() and (cron_dir / "CRON.md").exists():
                    cron_ids.append(cron_dir.name)
        except Exception:
            pass

        if not cron_ids:
            return None

        cron_list = "## Available Scheduled Tasks\n\n"
        for cron_id in sorted(cron_ids):
            cron_list += f"- `{cron_id}`\n"
        return cron_list

    def _build_memory_context(self) -> str | None:
        """Build a summary of what's in memory so the agent knows what it knows.

        Returns:
            Memory overview prompt or None if no memories exist.
        """
        memories_path = self.workspace_path / "memories"
        if not memories_path.exists():
            return None

        sections = []

        for axis in ("topics", "projects", "daily-notes"):
            axis_path = memories_path / axis
            if not axis_path.exists():
                continue
            keys = sorted(f.stem for f in axis_path.glob("*.md"))
            if keys:
                sections.append(f"- **{axis}**: {', '.join(keys)}")

        # Check reflections
        reflections_path = memories_path / "reflections"
        if reflections_path.exists():
            reflection_files = sorted(reflections_path.glob("*.md"))
            if reflection_files:
                sections.append(f"- **reflections**: {len(reflection_files)} entries")

        # Check learning journal
        journal_path = memories_path / "learning-journal"
        if journal_path.exists():
            journal_files = sorted(journal_path.glob("*.md"))
            if journal_files:
                sections.append(f"- **learning-journal**: {len(journal_files)} months")

        if not sections:
            return None

        return "## Your Memory\n\nYou have stored knowledge available. Use `memory_search` and `memory_recall` to access it.\n\n" + "\n".join(sections)

    def _build_runtime_context(self, agent_id: str, timestamp: float) -> str | None:
        """Build runtime context with current agent and timestamp info.

        Args:
            agent_id: Current agent identifier
            timestamp: Current Unix timestamp

        Returns:
            Runtime context prompt or None
        """
        dt = datetime.fromtimestamp(timestamp)
        formatted_time = dt.strftime("%Y-%m-%d %H:%M:%S %Z")

        context = f"""## Current Context

- **Active Agent**: {agent_id}
- **Timestamp**: {formatted_time}
- **Session Started**: Just now"""

        return context

    def _build_channel_hint(self, source: EventSource) -> str | None:
        """Build channel-specific hint based on event source.

        Args:
            source: The event source (cron, agent, platform, etc.)

        Returns:
            Channel hint or None
        """
        if isinstance(source, CronEventSource):
            return """## Execution Context

You are running as a background cron job. Your response will not be sent to user directly. Consider logging important results, updating memories, or dispatching to other agents."""

        if isinstance(source, AgentEventSource):
            return f"""## Execution Context

You are running as a dispatched subagent called by agent '{source.agent_id}'. Your response will be sent back to the parent agent."""

        if source.is_platform:
            platform_name = source.platform_name or "Unknown"
            return f"""## Execution Context

You are responding via {platform_name}. Keep responses concise and platform-appropriate."""

        return None
