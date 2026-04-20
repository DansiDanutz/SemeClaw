"""
SemeClaw CLI — onboard command

Detects agents and tools installed on the local device, then syncs them
into the War Room dashboard so they appear in the roster and board view.

Usage:
  semeclaw onboard
  semeclaw onboard --dry-run
  semeclaw onboard --clear
"""

from __future__ import annotations

import logging
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from semeclaw.onboarding.discovery import discover_all
from semeclaw.onboarding.seed import has_builtin_agents, seed_builtin_agents
from semeclaw.onboarding.sync import clear_discovered, sync_agents

logger = logging.getLogger("semeclaw.cli.onboard")
console = Console()

app = typer.Typer(help="Discover local agents and tools, sync to War Room")


def _format_table(agents: list) -> Table:
    table = Table(title="Discovered Agents & Tools")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Name", style="green")
    table.add_column("Kind", style="yellow")
    table.add_column("Status", style="magenta")
    table.add_column("Version", style="dim")
    table.add_column("Path / URL", style="blue")

    for a in agents:
        loc = a.url or a.path or "—"
        ver = a.version or "—"
        table.add_row(a.id, a.name, a.kind, a.status, ver, loc)
    return table


@app.callback(invoke_without_command=True)
def onboard(
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show what would change without writing")] = False,
    clear: Annotated[bool, typer.Option("--clear", help="Remove all discovered agents from state")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Log detailed discovery steps")] = False,
) -> None:
    """Scan the device for installed agents and sync them to the War Room."""
    if verbose:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    if clear:
        clear_discovered()
        console.print("[bold red]Cleared[/bold red] all discovered agents from War Room.")
        raise typer.Exit(0)

    console.print("[bold]Scanning device for agents and tools…[/bold]")
    agents = discover_all()

    # Always ensure built-in demo agents exist so the War Room works OOTB
    if not has_builtin_agents():
        seeded = seed_builtin_agents()
        console.print(f"[cyan]Seeded {len(seeded)} built-in demo agents:[/cyan] {', '.join(seeded)}")
    else:
        seeded = []

    if not agents and not seeded:
        console.print("[yellow]No agents or tools discovered.[/yellow]")
        console.print("Hint: Install OpenClaw, Paperclip, Hermes, or Multica, then run again.")
        raise typer.Exit(0)

    if agents:
        console.print(_format_table(agents))
        console.print()

    summary = sync_agents(agents, dry_run=dry_run)

    if dry_run:
        console.print(f"[bold]Dry run[/bold] — would create {len(summary['created'])}, update {len(summary['updated'])}, skip {len(summary['skipped'])}.")
    else:
        if agents:
            console.print(f"[bold green]Synced[/bold green] {summary['total']} discovered agents:")
            if summary["created"]:
                console.print(f"  Created: {', '.join(summary['created'])}")
            if summary["updated"]:
                console.print(f"  Updated: {', '.join(summary['updated'])}")
            if summary["skipped"]:
                console.print(f"  Skipped (unchanged): {', '.join(summary['skipped'])}")

    if seeded:
        console.print()
        console.print("[bold green]Built-in agents ready[/bold green] — the demo works without external tools.")

    console.print()
    console.print("Restart the War Room dashboard to see changes:")
    console.print("  [dim]python war_room/dashboard/server.py[/dim]")
