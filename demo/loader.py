"""
demo.loader — Demo mode data for SemeClaw War Room.

Provides pre-built agents, tasks, and report helpers so a fresh clone
can show a fully populated dashboard with zero API keys required.
"""

import os
import shutil
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_DEMO_DIR = Path(__file__).parent
_REPORTS_DIR = _DEMO_DIR / "reports"

# ---------------------------------------------------------------------------
# Demo agents — format matches the agent dicts used by the War Room dashboard
# ---------------------------------------------------------------------------
DEMO_AGENTS = [
    {
        "id": "aria",
        "name": "Aria",
        "role": "Strategic Orchestrator",
        "description": "Calm strategic thinker. Opens and closes every meeting.",
        "color": "#6366f1",
        "initials": "Ar",
        "status": "idle",
        "spentMonthlyCents": 0,
        "model": "ollama/qwen3:8b",
        "capabilities": ["orchestration", "planning", "decisions"],
    },
    {
        "id": "bolt",
        "name": "Bolt",
        "role": "Senior Developer",
        "description": "Blunt, fast, technical. Ships things.",
        "color": "#10b981",
        "initials": "Bo",
        "status": "idle",
        "spentMonthlyCents": 0,
        "model": "ollama/qwen3:8b",
        "capabilities": ["coding", "architecture", "devops"],
    },
    {
        "id": "luna",
        "name": "Luna",
        "role": "Product Manager",
        "description": "Organized, catches gaps, owns milestones.",
        "color": "#f59e0b",
        "initials": "Lu",
        "status": "idle",
        "spentMonthlyCents": 0,
        "model": "ollama/qwen3:8b",
        "capabilities": ["planning", "coordination", "documentation"],
    },
    {
        "id": "echo",
        "name": "Echo",
        "role": "Research Analyst",
        "description": "Data-driven, brings evidence, questions assumptions.",
        "color": "#06b6d4",
        "initials": "Ec",
        "status": "idle",
        "spentMonthlyCents": 0,
        "model": "ollama/qwen3:8b",
        "capabilities": ["research", "analysis", "competitive-intel"],
    },
]

# ---------------------------------------------------------------------------
# Demo tasks
# ---------------------------------------------------------------------------
DEMO_TASKS = [
    {
        "id": "demo-saas-landing",
        "title": "Build a SaaS Landing Page",
        "description": (
            "Design and build a high-converting landing page for a B2B SaaS product. "
            "Stack: Next.js + Tailwind + Framer Motion. Target: startup CTOs."
        ),
        "status": "pending",
        "agents": ["aria", "bolt", "luna", "echo"],
        "source": "demo/tasks/saas-landing-page.md",
    },
    {
        "id": "demo-competitor-analysis",
        "title": "Competitor Analysis — AI Project Management",
        "description": (
            "Produce a strategic brief on the top 5 competitors: "
            "Linear, Notion AI, Monday.com AI, Height, Asana AI."
        ),
        "status": "pending",
        "agents": ["aria", "echo", "luna"],
        "source": "demo/tasks/competitor-analysis.md",
    },
    {
        "id": "demo-api-sprint",
        "title": "API Integration Sprint — Stripe + Supabase",
        "description": (
            "Integrate Stripe billing and Supabase auth into the SaaS backend. "
            "Deploy to Fly.io."
        ),
        "status": "pending",
        "agents": ["aria", "bolt", "luna"],
        "source": "demo/tasks/api-integration-sprint.md",
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_demo_report_path() -> Path:
    """Return the path to the pre-built demo report markdown file."""
    return _REPORTS_DIR / "saas-landing-page-demo.md"


def is_demo_mode() -> bool:
    """Return True when DEMO_MODE env var is set to a truthy value."""
    return bool(os.environ.get("DEMO_MODE"))


def load_demo_into_war_room(war_room_dir: Path) -> None:
    """Copy demo report into war_room_dir/research/ so it appears in the dashboard.

    Creates the research directory if it does not exist.
    Skips the copy if the file is already present to avoid overwriting a real report.
    """
    research_dir = war_room_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)

    src = get_demo_report_path()
    dst = research_dir / src.name

    if not dst.exists():
        shutil.copy2(src, dst)
