"""Demo runner for SemeClaw — runs a sample War Room pipeline."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

# Bootstrap paths so war_room modules are importable
REPO_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from war_room.war_room import WarRoomPipeline
from paperclip_bridge import PaperclipBridge


async def run_demo(task: str, mock: bool = True) -> dict[str, Any] | None:
    """Run a demo War Room pipeline.

    Args:
        task: The task description to send through the pipeline.
        mock: If True, use mock mode for external services.

    Returns:
        Pipeline result dict or None.
    """
    print("🚀 Initializing War Room pipeline...")

    # Use a mock-friendly model if no real keys are available
    pipeline = WarRoomPipeline(model="ollama/qwen3:8b")

    print(f"📝 Task: {task}")
    print("🤖 Agents: research → strategist → writer")
    print()

    try:
        result = await pipeline.run(
            task=task,
            agents=["research", "strategist", "writer"],
            project="NERVIX",
            notify_telegram=False,
        )

        print("\n" + "═" * 60)
        print("📊 DEMO RESULTS")
        print("═" * 60)
        print(f"Run ID:     {result.run_id}")
        print(f"Task:       {result.task}")
        print(f"Agents:     {' → '.join(result.agents_run)}")
        print(f"Report:     {result.output_file}")

        if result.paperclip_issue:
            print(f"Paperclip:  {result.paperclip_issue.get('id', 'N/A')}")

        if result.results:
            print("\n📝 Agent outputs:")
            for agent_id, output in result.results.items():
                preview = output[:200].replace('\n', ' ') if output else "(no output)"
                print(f"  • {agent_id}: {preview}...")

        print("═" * 60)

        return {
            "run_id": result.run_id,
            "task": result.task,
            "agents_run": result.agents_run,
            "output_file": str(result.output_file) if result.output_file else None,
            "paperclip_issue": result.paperclip_issue,
            "results": result.results,
        }

    except Exception as exc:
        print(f"\n⚠️  Pipeline error: {exc}")
        print("\n💡 Tips:")
        print("  • Install Ollama: curl -fsSL https://ollama.com/install.sh | sh")
        print("  • Pull a model:   ollama pull qwen3:8b")
        print("  • Or set ANTHROPIC_API_KEY / OPENROUTER_API_KEY in .env")
        raise


if __name__ == "__main__":
    task = " ".join(sys.argv[1:]) or "Research open-source AI agent frameworks"
    asyncio.run(run_demo(task, mock=True))
