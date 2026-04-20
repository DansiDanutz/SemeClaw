"""
SemeClaw Onboarding — Seed built-in demo agents.

Ensures the War Room always has a minimum roster of 3 agents so the demo
meeting works out-of-the-box, even when no external agents are installed.
"""

from __future__ import annotations

from pathlib import Path

BUILTIN_AGENTS_DIR = Path("war_room/agents")

BUILTIN_AGENTS: dict[str, str] = {
    "research.md": """---
id: research
name: Dexter
role: Lead Researcher
emoji: "🔬"
color: "#60a5fa"
---

# Dexter — Lead Researcher

Hi, I'm Dexter. I pull the numbers, read the docs, and bring evidence.
I work in Dan's Lab War Room.
""",
    "strategist.md": """---
id: strategist
name: Memo
role: Strategist
emoji: "🎯"
color: "#34d399"
---

# Memo — Strategist

Hi, I'm Memo. I turn research into positions we can defend.
Wedge, moat, and what to say no to — that's my turf.
""",
    "writer.md": """---
id: writer
name: Hermes
role: Writer & Ticket-Drafter
emoji: "✍️"
color: "#f472b6"
---

# Hermes — Writer

Hi, I'm Hermes. I turn decisions into tickets the engineering team can ship.
Clean title, tight acceptance criteria, no ambiguity.
""",
}


def seed_builtin_agents(force: bool = False) -> list[str]:
    """Create built-in agent stubs if the agents directory is empty.

    Args:
        force: Overwrite existing built-in agent files even if they exist.

    Returns:
        List of agent IDs that were created.
    """
    BUILTIN_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    created: list[str] = []

    for filename, content in BUILTIN_AGENTS.items():
        path = BUILTIN_AGENTS_DIR / filename
        if not path.exists() or force:
            path.write_text(content, encoding="utf-8")
            created.append(path.stem)

    return created


def has_builtin_agents() -> bool:
    """Check whether at least 3 built-in agent files exist."""
    return sum(1 for name in BUILTIN_AGENTS if (BUILTIN_AGENTS_DIR / name).exists()) >= 3
