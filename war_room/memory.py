"""
War Room Memory — Cross-run knowledge persistence.

Saves research summaries after each pipeline run and loads relevant
context before new runs. Prevents duplicate work and builds cumulative
knowledge over time.

Storage:
  war_room/memory/memory.json  — index of all entries
  war_room/memory/entries/     — individual entry files (for large summaries)

Usage:
  from memory import WarRoomMemory
  mem = WarRoomMemory(Path("war_room/memory"))
  mem.save(topic="NERVIX pricing", summary="...", run_id="abc123", file="research/...")
  entries = mem.load_relevant("pricing strategy for AI marketplace")
  all_entries = mem.load_all()
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("war_room.memory")

# How many prior entries to inject into a new run's context
MAX_MEMORY_ENTRIES = 5

# Minimum keyword overlap score to consider an entry relevant
MIN_RELEVANCE_SCORE = 1


class WarRoomMemory:
    """
    Simple keyword-based memory for War Room runs.

    Each memory entry contains:
      - topic:    Short description of what was researched/built
      - keywords: Auto-extracted keywords for relevance matching
      - summary:  Compact summary of findings (first 800 chars of agent outputs)
      - run_id:   Which pipeline run produced this
      - file:     Path to the full report markdown
      - date:     ISO timestamp
    """

    def __init__(self, memory_dir: Path):
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.memory_dir / "memory.json"

    # ------------------------------------------------------------------ #
    #  Write                                                               #
    # ------------------------------------------------------------------ #

    def save(
        self,
        topic: str,
        summary: str,
        run_id: str,
        file: str = "",
    ) -> dict:
        """Save a new memory entry. Returns the saved entry."""
        entry = {
            "topic":    topic,
            "keywords": self._extract_keywords(topic + " " + summary),
            "summary":  summary[:800],
            "run_id":   run_id,
            "file":     file,
            "date":     datetime.utcnow().isoformat(),
        }

        entries = self._load_index()
        entries.append(entry)
        self._save_index(entries)

        logger.debug("Memory saved: %s (run %s)", topic[:60], run_id)
        return entry

    # ------------------------------------------------------------------ #
    #  Read                                                                #
    # ------------------------------------------------------------------ #

    def load_relevant(self, task: str, max_entries: int = MAX_MEMORY_ENTRIES) -> list[dict]:
        """
        Load memory entries relevant to the given task.
        Uses keyword overlap scoring — returns top matches.
        """
        entries = self._load_index()
        if not entries:
            return []

        task_keywords = set(self._extract_keywords(task))
        if not task_keywords:
            # No keywords extracted — return most recent entries
            return entries[-max_entries:]

        scored = []
        for entry in entries:
            entry_keywords = set(entry.get("keywords", []))
            overlap = len(task_keywords & entry_keywords)
            if overlap >= MIN_RELEVANCE_SCORE:
                scored.append((overlap, entry))

        # Sort by relevance descending, then recency
        scored.sort(key=lambda x: (-x[0], x[1]["date"]), reverse=False)
        top = [e for _, e in scored[-max_entries:]]

        logger.debug(
            "Memory: %d/%d entries relevant to task '%s'",
            len(top), len(entries), task[:50],
        )
        return top

    def load_all(self) -> list[dict]:
        """Return all memory entries, oldest first."""
        return self._load_index()

    def clear(self) -> int:
        """Delete all memory entries. Returns count deleted."""
        entries = self._load_index()
        count = len(entries)
        self._save_index([])
        logger.info("Memory cleared: %d entries removed", count)
        return count

    def stats(self) -> dict:
        entries = self._load_index()
        return {
            "total_entries": len(entries),
            "oldest": entries[0]["date"][:10] if entries else None,
            "newest": entries[-1]["date"][:10] if entries else None,
            "topics": [e["topic"][:60] for e in entries[-10:]],
        }

    # ------------------------------------------------------------------ #
    #  Internal                                                            #
    # ------------------------------------------------------------------ #

    def _load_index(self) -> list[dict]:
        if not self.index_file.exists():
            return []
        try:
            return json.loads(self.index_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Failed to load memory index: %s", e)
            return []

    def _save_index(self, entries: list[dict]) -> None:
        self.index_file.write_text(
            json.dumps(entries, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _extract_keywords(self, text: str) -> list[str]:
        """
        Extract meaningful keywords from text for relevance matching.
        Lowercases, strips punctuation, removes stop words, deduplicates.
        """
        STOP_WORDS = {
            "a", "an", "the", "and", "or", "but", "in", "on", "at", "to",
            "for", "of", "with", "by", "from", "is", "are", "was", "were",
            "be", "been", "being", "have", "has", "had", "do", "does", "did",
            "will", "would", "could", "should", "may", "might", "shall", "can",
            "that", "this", "these", "those", "it", "its", "we", "our", "you",
            "your", "they", "their", "how", "what", "when", "where", "which",
            "who", "why", "all", "any", "each", "not", "as", "if", "so", "up",
            "out", "about", "into", "than", "then", "also", "just", "now",
            "new", "use", "used", "using", "get", "one", "two", "three",
            "task", "agent", "output", "context", "run", "research", "based",
        }
        # Tokenise
        words = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", text.lower())
        keywords = []
        seen = set()
        for w in words:
            if w not in STOP_WORDS and w not in seen:
                keywords.append(w)
                seen.add(w)
        return keywords[:50]  # cap at 50 keywords per entry
