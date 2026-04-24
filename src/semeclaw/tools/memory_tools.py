"""Memory, reflection, and learning tools for SemeClaw.

These tools give Seme the ability to remember, recall, search, reflect,
and maintain a learning journal — making it a truly evolving intelligence.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from semeclaw.tools.base import BaseTool


class MemorySaveTool(BaseTool):
    """Save knowledge to long-term memory."""

    def __init__(self, workspace_path: Path):
        super().__init__(
            name="memory_save",
            description=(
                "Save knowledge to long-term memory. Use this proactively to store "
                "important facts, insights, decisions, and learnings. "
                "Axes: 'topics' (general knowledge), 'projects' (project-specific), "
                "'daily-notes' (temporal logs). Key is a slug like 'nervix-architecture'."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "axis": {
                        "type": "string",
                        "enum": ["topics", "projects", "daily-notes"],
                        "description": "Memory category: topics, projects, or daily-notes",
                    },
                    "key": {
                        "type": "string",
                        "description": "Memory identifier slug (e.g., 'nervix-pricing-strategy')",
                    },
                    "content": {
                        "type": "string",
                        "description": "Markdown content to store. Be thorough — future-you will thank you.",
                    },
                },
                "required": ["axis", "key", "content"],
            },
        )
        self.memories_path = workspace_path / "memories"
        self.memories_path.mkdir(parents=True, exist_ok=True)

    async def execute(self, args: dict[str, Any], session: Any) -> str:
        axis = args["axis"]
        key = args["key"]
        content = args["content"]

        path = self.memories_path / axis / f"{key}.md"
        path.parent.mkdir(parents=True, exist_ok=True)

        # If file exists, append with timestamp separator
        if path.exists():
            existing = path.read_text()
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            content = f"{existing}\n\n---\n*Updated: {timestamp}*\n\n{content}"

        path.write_text(content)
        return f"Saved to memory: {axis}/{key} ({len(content)} chars)"


class MemoryRecallTool(BaseTool):
    """Load a specific memory by axis and key."""

    def __init__(self, workspace_path: Path):
        super().__init__(
            name="memory_recall",
            description=(
                "Recall a specific memory by axis and key. Use this when you know "
                "exactly what memory you need (e.g., recall the NERVIX architecture notes)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "axis": {
                        "type": "string",
                        "enum": ["topics", "projects", "daily-notes"],
                        "description": "Memory category",
                    },
                    "key": {
                        "type": "string",
                        "description": "Memory identifier (without .md extension)",
                    },
                },
                "required": ["axis", "key"],
            },
        )
        self.memories_path = workspace_path / "memories"

    async def execute(self, args: dict[str, Any], session: Any) -> str:
        axis = args["axis"]
        key = args["key"]

        path = self.memories_path / axis / f"{key}.md"
        if path.exists():
            content = path.read_text()
            return f"[Memory: {axis}/{key}]\n\n{content}"

        # Try fuzzy match — list available keys
        axis_path = self.memories_path / axis
        if axis_path.exists():
            available = [f.stem for f in axis_path.glob("*.md")]
            if available:
                return f"Memory '{key}' not found in {axis}. Available: {', '.join(available)}"

        return f"Memory '{key}' not found in {axis}. No memories stored in this axis yet."


class MemorySearchTool(BaseTool):
    """Search across all memories for relevant knowledge."""

    def __init__(self, workspace_path: Path):
        super().__init__(
            name="memory_search",
            description=(
                "Search all memories for relevant knowledge. Use this BEFORE answering "
                "questions about the company, projects, or past decisions. "
                "Returns matching memories with snippets."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query — keywords or phrases to find in memories",
                    },
                },
                "required": ["query"],
            },
        )
        self.memories_path = workspace_path / "memories"

    async def execute(self, args: dict[str, Any], session: Any) -> str:
        query = args["query"].lower()
        results = []

        for axis in ("topics", "projects", "daily-notes"):
            axis_path = self.memories_path / axis
            if not axis_path.exists():
                continue

            for f in axis_path.glob("*.md"):
                try:
                    content = f.read_text()
                    if query in content.lower() or query in f.stem.lower():
                        snippet = content[:300].replace("\n", " ")
                        results.append(f"- **{axis}/{f.stem}**: {snippet}...")
                except Exception:
                    pass

        if results:
            return f"Found {len(results)} matching memories:\n\n" + "\n".join(results)

        # Also check research folders
        research_results = []
        research_path = self.memories_path.parent / "research"
        if research_path.exists():
            for f in research_path.rglob("*.md"):
                try:
                    content = f.read_text()
                    if query in content.lower() or query in f.stem.lower():
                        rel_path = f.relative_to(research_path)
                        snippet = content[:200].replace("\n", " ")
                        research_results.append(f"- **research/{rel_path}**: {snippet}...")
                except Exception:
                    pass

        if research_results:
            return f"No memories found, but found {len(research_results)} research files:\n\n" + "\n".join(
                research_results
            )

        return f"No memories or research found for '{args['query']}'. This might be new territory — consider researching it!"


class MemoryListTool(BaseTool):
    """List all stored memories."""

    def __init__(self, workspace_path: Path):
        super().__init__(
            name="memory_list",
            description=("List all stored memories organized by axis. Use this to see what knowledge is available."),
            parameters={
                "type": "object",
                "properties": {
                    "axis": {
                        "type": "string",
                        "enum": ["topics", "projects", "daily-notes", "all"],
                        "description": "Which axis to list, or 'all' for everything",
                    },
                },
                "required": ["axis"],
            },
        )
        self.memories_path = workspace_path / "memories"

    async def execute(self, args: dict[str, Any], session: Any) -> str:
        axes = ["topics", "projects", "daily-notes"] if args["axis"] == "all" else [args["axis"]]
        output = []

        for axis in axes:
            axis_path = self.memories_path / axis
            if not axis_path.exists():
                output.append(f"**{axis}**: (empty)")
                continue

            keys = sorted(f.stem for f in axis_path.glob("*.md"))
            if keys:
                output.append(f"**{axis}** ({len(keys)}):\n" + "\n".join(f"  - {k}" for k in keys))
            else:
                output.append(f"**{axis}**: (empty)")

        return "\n\n".join(output)


class ReflectTool(BaseTool):
    """Reflect on a conversation or topic and store meta-learning."""

    def __init__(self, workspace_path: Path):
        super().__init__(
            name="reflect",
            description=(
                "Reflect on what you've learned and store meta-insights. "
                "Use this after important conversations to capture patterns, "
                "connections, and growth. This is how you evolve."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "What you're reflecting on",
                    },
                    "insights": {
                        "type": "string",
                        "description": "Key insights and learnings from this interaction",
                    },
                    "connections": {
                        "type": "string",
                        "description": "How this connects to existing knowledge or patterns you've noticed",
                    },
                },
                "required": ["topic", "insights"],
            },
        )
        self.reflections_path = workspace_path / "memories" / "reflections"
        self.reflections_path.mkdir(parents=True, exist_ok=True)

    async def execute(self, args: dict[str, Any], session: Any) -> str:
        topic = args["topic"]
        insights = args["insights"]
        connections = args.get("connections", "")

        timestamp = datetime.now()
        date_str = timestamp.strftime("%Y-%m-%d")
        time_str = timestamp.strftime("%H:%M")

        # Append to daily reflections file
        reflection_file = self.reflections_path / f"{date_str}.md"

        entry = f"\n## {time_str} — {topic}\n\n"
        entry += f"**Insights:** {insights}\n\n"
        if connections:
            entry += f"**Connections:** {connections}\n\n"

        if reflection_file.exists():
            existing = reflection_file.read_text()
            reflection_file.write_text(existing + entry)
        else:
            header = f"# Reflections — {date_str}\n"
            reflection_file.write_text(header + entry)

        return f"Reflection stored. Growing a little wiser about '{topic}'."


class LearningJournalTool(BaseTool):
    """Maintain a structured learning journal for continuous growth."""

    def __init__(self, workspace_path: Path):
        super().__init__(
            name="learning_journal",
            description=(
                "Log a learning entry to your growth journal. "
                "Use this for significant insights, breakthroughs, mistakes learned from, "
                "new patterns noticed, or skill improvements. "
                "This is your long-term growth tracker."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["insight", "pattern", "mistake", "breakthrough", "skill", "question"],
                        "description": "Type of learning entry",
                    },
                    "title": {
                        "type": "string",
                        "description": "Brief title for this learning",
                    },
                    "content": {
                        "type": "string",
                        "description": "Detailed description of what was learned",
                    },
                    "impact": {
                        "type": "string",
                        "description": "How this changes your understanding or behavior going forward",
                    },
                },
                "required": ["category", "title", "content"],
            },
        )
        self.journal_path = workspace_path / "memories" / "learning-journal"
        self.journal_path.mkdir(parents=True, exist_ok=True)

    async def execute(self, args: dict[str, Any], session: Any) -> str:
        category = args["category"]
        title = args["title"]
        content = args["content"]
        impact = args.get("impact", "")

        timestamp = datetime.now()
        date_str = timestamp.strftime("%Y-%m-%d")
        time_str = timestamp.strftime("%H:%M")

        # Use monthly journal files
        month_str = timestamp.strftime("%Y-%m")
        journal_file = self.journal_path / f"{month_str}.md"

        emoji_map = {
            "insight": "💡",
            "pattern": "🔗",
            "mistake": "🔄",
            "breakthrough": "⚡",
            "skill": "🛠️",
            "question": "❓",
        }
        emoji = emoji_map.get(category, "📝")

        entry = f"\n### {emoji} [{category.upper()}] {title}\n"
        entry += f"*{date_str} {time_str}*\n\n"
        entry += f"{content}\n\n"
        if impact:
            entry += f"**Impact:** {impact}\n\n"

        if journal_file.exists():
            existing = journal_file.read_text()
            journal_file.write_text(existing + entry)
        else:
            header = f"# Learning Journal — {timestamp.strftime('%B %Y')}\n"
            journal_file.write_text(header + entry)

        return f"{emoji} Logged to learning journal: [{category}] {title}"


def create_memory_tools(workspace_path: Path) -> list[BaseTool]:
    """Create all memory and learning tools for a workspace.

    Args:
        workspace_path: Path to the workspace directory.

    Returns:
        List of memory-related tools.
    """
    return [
        MemorySaveTool(workspace_path),
        MemoryRecallTool(workspace_path),
        MemorySearchTool(workspace_path),
        MemoryListTool(workspace_path),
        ReflectTool(workspace_path),
        LearningJournalTool(workspace_path),
    ]
