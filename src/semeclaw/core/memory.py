"""File-based memory storage for SemeClaw."""

from __future__ import annotations

from pathlib import Path


class MemoryStore:
    """Simple file-based memory storage for DansLab knowledge.

    Organizes memories into three axes:
    - topics: General knowledge and concepts
    - projects: Project-specific information
    - daily-notes: Daily logs and ephemeral notes
    """

    def __init__(self, memories_path: Path) -> None:
        """Initialize MemoryStore.

        Args:
            memories_path: Base path for storing memories
        """
        self.base_path = Path(memories_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

        # Create standard axes
        for axis in ("topics", "projects", "daily-notes"):
            (self.base_path / axis).mkdir(exist_ok=True)

    def save(self, axis: str, key: str, content: str) -> Path:
        """Save a memory to disk.

        Args:
            axis: Memory axis (topics, projects, or daily-notes)
            key: Memory key/identifier (e.g., "project-x-status")
            content: Memory content as markdown

        Returns:
            Path where memory was saved
        """
        path = self.base_path / axis / f"{key}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def load(self, axis: str, key: str) -> str | None:
        """Load a memory from disk.

        Args:
            axis: Memory axis (topics, projects, or daily-notes)
            key: Memory key/identifier

        Returns:
            Memory content as string, or None if not found
        """
        path = self.base_path / axis / f"{key}.md"
        if path.exists():
            return path.read_text()
        return None

    def list_keys(self, axis: str) -> list[str]:
        """List all keys in a given axis.

        Args:
            axis: Memory axis to list

        Returns:
            List of memory keys (without .md extension)
        """
        axis_path = self.base_path / axis
        if not axis_path.exists():
            return []
        return [f.stem for f in axis_path.glob("*.md")]

    def search(self, query: str) -> list[tuple[str, str, str]]:
        """Search for memories across all axes.

        Performs simple case-insensitive text search.

        Args:
            query: Search query string

        Returns:
            List of (axis, key, snippet) tuples. Snippet is first 200 chars.
        """
        results: list[tuple[str, str, str]] = []
        query_lower = query.lower()

        for axis in ("topics", "projects", "daily-notes"):
            axis_path = self.base_path / axis
            if not axis_path.exists():
                continue

            for f in axis_path.glob("*.md"):
                try:
                    content = f.read_text()
                    if query_lower in content.lower():
                        snippet = content[:200]
                        results.append((axis, f.stem, snippet))
                except Exception:
                    # Skip unreadable files
                    pass

        return results

    def delete(self, axis: str, key: str) -> bool:
        """Delete a memory from disk.

        Args:
            axis: Memory axis
            key: Memory key to delete

        Returns:
            True if deleted, False if not found
        """
        path = self.base_path / axis / f"{key}.md"
        if path.exists():
            path.unlink()
            return True
        return False
