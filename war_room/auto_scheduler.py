"""
War Room — Autonomous Scheduler

Runs nightly. Picks the highest-priority open task from Paperclip
(NERVIX project) and runs it through the War Room pipeline automatically.

Skip logic:
  - Skips tasks already processed (tracked in memory)
  - Skips tasks with label 'skip-war-room'
  - Skips if no open tasks found

Usage:
  uv run python war_room/auto_scheduler.py
  uv run python war_room/auto_scheduler.py --dry-run
  uv run python war_room/auto_scheduler.py --project=CrawdBot
"""

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
WAR_ROOM_DIR = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(WAR_ROOM_DIR))

from memory import WarRoomMemory
from paperclip_bridge import load_bridge

from war_room import WarRoomPipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [auto_scheduler] %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("auto_scheduler")

SCHEDULER_LOG = WAR_ROOM_DIR / "logs" / "auto_scheduler.jsonl"
SKIP_LABELS = {"skip-war-room", "wip", "blocked", "on-hold"}
MAX_TASK_AGE_DAYS = 30  # ignore tasks older than this
DEFAULT_PROJECT = "NERVIX"


def _log_run(entry: dict) -> None:
    SCHEDULER_LOG.parent.mkdir(exist_ok=True)
    with SCHEDULER_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _already_processed(task_title: str, memory: WarRoomMemory) -> bool:
    """Check if this exact task was already run through the pipeline."""
    entries = memory.load_all()
    title_lower = task_title.lower()
    for e in entries:
        if title_lower in e.get("topic", "").lower():
            return True
    return False


async def pick_task(project: str, bridge) -> dict | None:
    """
    Pick the highest-priority open task from Paperclip.
    Priority order: urgent → high → medium → low
    Skips tasks with skip labels.
    Returns the chosen issue dict or None.
    """
    PRIORITY_ORDER = ["urgent", "high", "medium", "low"]

    try:
        issues = await bridge.get_project_issues(project)
    except Exception as e:
        logger.error("Failed to fetch issues from Paperclip: %s", e)
        return None

    if not issues:
        logger.info("No issues found for project %s", project)
        return None

    # Filter: only open/todo issues, skip blocked/wip labels
    open_issues = []
    for issue in issues:
        status = (issue.get("status") or issue.get("state") or "").lower()
        labels = {(l.lower() if isinstance(l, str) else l.get("name", "").lower()) for l in issue.get("labels", [])}
        if status in ("done", "closed", "completed", "cancelled"):
            continue
        if labels & SKIP_LABELS:
            continue
        open_issues.append(issue)

    if not open_issues:
        logger.info("No eligible open issues in %s", project)
        return None

    # Sort by priority
    priority_map = {p: i for i, p in enumerate(PRIORITY_ORDER)}
    open_issues.sort(key=lambda x: priority_map.get((x.get("priority") or "medium").lower(), 99))

    chosen = open_issues[0]
    logger.info(
        "Picked task: [%s] %s (priority=%s)",
        chosen.get("id", "?"),
        chosen.get("title", "?")[:60],
        chosen.get("priority", "?"),
    )
    return chosen


async def run(project: str = DEFAULT_PROJECT, dry_run: bool = False) -> None:
    memory = WarRoomMemory(WAR_ROOM_DIR / "memory")

    logger.info("🤖 Auto-scheduler starting | project=%s | dry_run=%s", project, dry_run)
    logger.info("   Time: %s EET", datetime.now().strftime("%Y-%m-%d %H:%M"))

    bridge = load_bridge()
    async with bridge:
        task_issue = await pick_task(project, bridge)

    if not task_issue:
        logger.info("✋ Nothing to run — no eligible tasks")
        _log_run({"date": datetime.now(timezone.utc).isoformat(), "project": project, "result": "no_tasks"})
        return

    task_title = task_issue.get("title", "")
    task_desc = task_issue.get("description", "")
    issue_id = task_issue.get("id", "?")

    # Build a rich task prompt for the pipeline
    task_prompt = task_title
    if task_desc:
        task_prompt += f"\n\nContext from Paperclip issue {issue_id}:\n{task_desc[:800]}"

    # Check if already processed
    if _already_processed(task_title, memory):
        logger.info("⏭  Task already in memory — skipping: %s", task_title[:60])
        _log_run(
            {
                "date": datetime.now(timezone.utc).isoformat(),
                "project": project,
                "issue_id": issue_id,
                "result": "already_processed",
                "task": task_title,
            }
        )
        return

    if dry_run:
        logger.info("🔍 DRY RUN — would run pipeline for: %s", task_title[:80])
        print(f"\nWould run: {task_prompt[:200]}\n")
        return

    # Determine pipeline based on task content
    task_lower = task_title.lower() + " " + task_desc.lower()
    if any(w in task_lower for w in ("implement", "build", "create", "add feature", "fix", "refactor", "migrate")):
        agents = ["research", "architect", "coder", "writer"]
        logger.info("🛠  Code task detected — using full pipeline with Coder agent")
    else:
        agents = ["research", "strategist", "writer"]
        logger.info("🔬 Research/strategy task — using standard pipeline")

    logger.info("🚀 Launching pipeline | issue=%s | agents=%s", issue_id, " → ".join(agents))

    pipeline = WarRoomPipeline()
    try:
        result = await pipeline.run(
            task=task_prompt,
            agents=agents,
            project=project,
            notify_telegram=True,
        )
        logger.info("✅ Pipeline complete | run_id=%s | report=%s", result["run_id"], Path(result["output_file"]).name)
        _log_run(
            {
                "date": datetime.now(timezone.utc).isoformat(),
                "project": project,
                "issue_id": issue_id,
                "run_id": result["run_id"],
                "agents": agents,
                "result": "success",
                "task": task_title,
                "report": result["output_file"],
            }
        )
    except Exception as e:
        logger.error("❌ Pipeline failed: %s", e)
        _log_run(
            {
                "date": datetime.now(timezone.utc).isoformat(),
                "project": project,
                "issue_id": issue_id,
                "result": "failed",
                "error": str(e),
                "task": task_title,
            }
        )
        raise


def main() -> None:
    args = sys.argv[1:]
    project = DEFAULT_PROJECT
    dry_run = False
    for a in args:
        if a.startswith("--project="):
            project = a.split("=", 1)[1]
        elif a == "--dry-run":
            dry_run = True
    asyncio.run(run(project=project, dry_run=dry_run))


if __name__ == "__main__":
    main()
