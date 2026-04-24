"""Demo runner for SemeClaw — runs sample War Room scenarios."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

# Bootstrap paths
REPO_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


def _print_scenario_card(scenario: dict) -> None:
    """Print a nice meeting card for the scenario."""
    card = scenario.get("meeting_card", {})
    attendees = card.get("attendees", [])

    console.print()
    console.print(
        Panel(
            f"[bold cyan]{card.get('title', scenario['title'])}[/bold cyan]\n"
            f"[dim]{card.get('agenda', scenario.get('task', ''))}[/dim]",
            border_style="cyan",
            padding=(1, 2),
        )
    )

    if attendees:
        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        table.add_column("Agent", style="bold")
        table.add_column("Role")
        table.add_column("Voice")

        for a in attendees:
            table.add_row(
                f"{a.get('emoji', '🤖')} {a.get('name', a['id'])}",
                a.get("role", ""),
                a.get("voice", "default")[:20],
            )
        console.print(table)

    console.print(f"[dim]Kickoff:[/dim] {card.get('kickoff', 'Starting meeting...')}")
    console.print()


def _print_turn(turn: dict, index: int) -> None:
    """Print a single dialog turn."""
    kind = turn.get("kind", "speak")
    agent = turn.get("agent", "unknown")
    message = turn.get("message", "")
    delay = turn.get("delay_ms", 0)

    emoji = {"narrator": "🎙", "research": "🔬", "strategist": "🎯", "writer": "✍️", "architect": "🏗"}.get(agent, "💬")
    name = {
        "narrator": "Orchestrator",
        "research": "Dexter",
        "strategist": "Memo",
        "writer": "Hermes",
        "architect": "David",
    }.get(agent, agent)

    if kind == "intro":
        console.print(f"\n[bold yellow]{emoji} {name}[/bold yellow] [dim](intro)[/dim]")
    elif kind == "outro":
        console.print(f"\n[bold yellow]{emoji} {name}[/bold yellow] [dim](outro)[/dim]")
    elif kind == "handoff":
        console.print(f"[dim]→ {message}[/dim]")
        return
    else:
        console.print(f"\n[bold green]{emoji} {name}[/bold green]")

    if message:
        console.print(f"{message}")

    if delay:
        # In a real demo we'd async sleep here; for CLI we just note it
        pass


async def run_demo(task: str = "", mock: bool = True) -> dict[str, Any] | None:
    """Run a demo War Room scenario.

    Args:
        task: Ignored — demo always runs the 'welcome' scenario.
        mock: Ignored — demo uses scripted scenarios.

    Returns:
        Demo result dict.
    """
    # Import demo scripts
    try:
        from war_room.dashboard.demo_scripts import get_scenario, list_scenarios
    except ImportError:
        console.print("[red]Demo scripts not found.[/red]")
        return None

    scenarios = list_scenarios()
    if not scenarios:
        console.print("[red]No demo scenarios available.[/red]")
        return None

    # Show available scenarios
    console.print()
    console.print("[bold cyan]🎭 SemeClaw Demo — Available Scenarios[/bold cyan]")
    console.print()

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("#", style="dim", width=3)
    table.add_column("Scenario")
    table.add_column("Agents")

    for i, s in enumerate(scenarios, 1):
        agents = ", ".join(s.get("agents", []))
        table.add_row(str(i), s.get("title", s["id"]), agents)

    console.print(table)
    console.print()

    # Default to welcome scenario
    scenario_id = "welcome"
    scenario = get_scenario(scenario_id)
    if not scenario:
        console.print(f"[red]Scenario '{scenario_id}' not found.[/red]")
        return None

    # Print scenario card
    _print_scenario_card(scenario)

    # Play through turns
    console.print("[bold]─[/bold]" * 60)
    console.print("[bold]Meeting transcript[/bold]")
    console.print("[bold]─[/bold]" * 60)

    for i, turn in enumerate(scenario.get("turns", [])):
        _print_turn(turn, i)

    console.print()
    console.print("[bold]─[/bold]" * 60)
    console.print(f"[green]✓ Demo complete[/green] — scenario: [bold]{scenario['title']}[/bold]")
    console.print("[dim]In the real War Room, these agents run actual LLM calls and produce shippable tickets.[/dim]")
    console.print()
    console.print("[dim]Next steps:[/dim]")
    console.print("  [cyan]semeclaw init[/cyan]     → configure providers for real agents")
    console.print("  [cyan]semeclaw war-room[/cyan] → start the dashboard")
    console.print("  [cyan]semeclaw chat[/cyan]     → talk to SemeClaw directly")
    console.print()

    return {
        "scenario": scenario_id,
        "title": scenario["title"],
        "agents": scenario.get("agents", []),
        "turns_count": len(scenario.get("turns", [])),
    }


if __name__ == "__main__":
    asyncio.run(run_demo())
