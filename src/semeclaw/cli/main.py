"""CLI interface for SemeClaw using Typer."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from semeclaw.cli.chat import chat_command
from semeclaw.cli.onboard import app as onboard_app
from semeclaw.meeting.room import MeetingRoom
from semeclaw.utils.config import Config

app = typer.Typer(
    name="semeclaw",
    help="SemeClaw: The AI Brain of DansLab Company",
    no_args_is_help=True,
    add_completion=True,
)

# Register subcommands
app.add_typer(onboard_app, name="onboard")

console = Console()

# Commands that do NOT require a pre-loaded config
_NO_CONFIG_COMMANDS = {"init", "demo", "war-room"}


def workspace_callback(ctx: typer.Context, workspace: str) -> Path:
    ctx.ensure_object(dict)
    ctx.obj["workspace"] = Path(workspace)
    return Path(workspace)


@app.callback()
def main(
    ctx: typer.Context,
    workspace: str = typer.Option(
        "default_workspace",
        "--workspace",
        "-w",
        help="Path to workspace directory",
        callback=workspace_callback,
    ),
) -> None:
    """Configuration is loaded from workspace/config.user.yaml by default."""
    # Let init run without a config
    if ctx.invoked_subcommand in _NO_CONFIG_COMMANDS:
        return

    workspace_path = ctx.obj["workspace"]
    config_file = workspace_path / "config.user.yaml"

    if not config_file.exists():
        console.print(
            f"\n[yellow]No config found at [bold]{config_file}[/bold][/yellow]\n"
            "Run [bold cyan]semeclaw init[/bold cyan] to set up SemeClaw.\n"
        )
        raise typer.Exit(1)

    try:
        cfg = Config.load(workspace_path)
        ctx.obj["config"] = cfg
    except Exception as e:
        console.print(f"[red]Error loading config:[/red] {e}")
        raise typer.Exit(1)


@app.command("init")
def init(
    ctx: typer.Context,
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Overwrite existing config without prompting"),
    ] = False,
) -> None:
    """Set up SemeClaw: auto-detect providers and create config.user.yaml.

    Scans your environment for API keys, probes local services (Ollama),
    walks you through choosing a provider and model, validates the connection,
    and writes a ready-to-use config.

    Examples:
        semeclaw init
        semeclaw init --workspace ./my-workspace
        semeclaw init --force
    """
    from semeclaw.cli.onboard import run_onboard

    workspace_path = ctx.obj["workspace"]
    success = run_onboard(workspace_path, force=force)
    if not success:
        raise typer.Exit(1)


@app.command("chat")
def chat(
    ctx: typer.Context,
    agent: Annotated[
        str | None,
        typer.Option("--agent", "-a", help="Agent ID (overrides default_agent from config)"),
    ] = None,
) -> None:
    """Start an interactive chat session with SemeClaw."""
    chat_command(ctx, agent_id=agent)


@app.command("demo")
def demo(
    ctx: typer.Context,
    task: Annotated[
        str,
        typer.Option("--task", "-t", help="Demo task to run"),
    ] = "Research open-source AI agent frameworks and write a positioning brief",
    mock: Annotated[
        bool,
        typer.Option("--mock", "-m", help="Use mock mode (no API calls)"),
    ] = False,
) -> None:
    """Run a demo War Room pipeline with sample agents.

    Uses mock mode by default so it works without API keys.
    Set --mock=False to run with real LLM calls.

    Examples:
        semeclaw demo
        semeclaw demo --task "Build a telemetry pipeline" --mock
    """
    import asyncio
    from semeclaw.cli.demo_runner import run_demo

    console.print()
    console.print("[bold cyan]🎭 SemeClaw Demo[/bold cyan]")
    console.print(f"[dim]Task:[/dim] {task}")
    console.print()

    try:
        result = asyncio.run(run_demo(task, mock=mock))
        if result:
            console.print(f"\n[green]✓ Demo complete[/green]")
            if result.get("output_file"):
                console.print(f"[dim]Report:[/dim] {result['output_file']}")
    except Exception as e:
        console.print(f"[red]Demo failed:[/red] {e}")
        raise typer.Exit(1)


@app.command("meeting")
def meeting(
    ctx: typer.Context,
    topic: Annotated[
        str,
        typer.Option("--topic", "-t", help="Meeting topic / agenda"),
    ] = "NERVIX Strategy Session",
    agents: Annotated[
        str,
        typer.Option("--agents", "-a", help="Comma-separated agent IDs"),
    ] = "seme,cookie,researcher",
    mock: Annotated[
        bool,
        typer.Option("--mock", "-m", help="Run in mock mode (no API calls)"),
    ] = False,
) -> None:
    """Run a multi-agent meeting with NERVIX branding.

    Orchestrates agents to discuss a topic, synthesizes a summary,
    and saves the report to research/meetings/.

    Plays ambient background music during the meeting and a brand
    jingle when the NERVIX advertisement is shown.

    Examples:
        semeclaw meeting
        semeclaw meeting --topic "Q3 Roadmap" --agents "seme,cookie"
        semeclaw meeting --topic "Test" --mock
    """
    config = ctx.obj.get("config")
    if not config:
        console.print("[red]Config not loaded. Run semeclaw init first.[/red]")
        raise typer.Exit(1)

    agent_ids = [a.strip() for a in agents.split(",") if a.strip()]
    room = MeetingRoom(topic=topic, agent_ids=agent_ids, config=config)

    try:
        result = asyncio.run(room.generate_context(mock=mock))
        console.print(f"\n[green]Meeting complete![/green]")
        console.print(f"[dim]Report:[/dim] {result}")
    except Exception as e:
        console.print(f"[red]Meeting failed:[/red] {e}")
        raise typer.Exit(1)


@app.command("war-room")
def war_room(
    ctx: typer.Context,
    host: Annotated[
        str,
        typer.Option("--host", "-h", help="Host to bind to"),
    ] = "0.0.0.0",
    port: Annotated[
        int,
        typer.Option("--port", "-p", help="Port to bind to"),
    ] = 8765,
) -> None:
    """Start the War Room dashboard server.

    Serves the dashboard, API, and WebSocket endpoints.

    Example:
        semeclaw war-room
        semeclaw war-room --port 8080
    """
    import subprocess
    import sys

    # Resolution order: Docker path → repo path → install path
    dashboard_script = (
        Path("/app/dashboard/server.py")
        if Path("/app/dashboard/server.py").exists()
        else Path(__file__).parent.parent.parent.parent / "war_room" / "dashboard" / "server.py"
    )
    if not dashboard_script.exists():
        # Try relative to install
        dashboard_script = Path(sys.prefix) / "war_room" / "dashboard" / "server.py"
    if not dashboard_script.exists():
        # Docker /app fallback
        dashboard_script = Path.cwd() / "war_room" / "dashboard" / "server.py"

    if not dashboard_script.exists():
        console.print(f"[red]Dashboard server not found at {dashboard_script}[/red]")
        console.print("[dim]Expected one of:[/dim]")
        console.print("  - /app/dashboard/server.py  (Docker)")
        console.print("  - ./war_room/dashboard/server.py  (repo)")
        raise typer.Exit(1)

    console.print(f"[bold cyan]🚀 Starting War Room dashboard on {host}:{port}...[/bold cyan]")
    console.print(f"[dim]Dashboard: http://{host}:{port}[/dim]")
    console.print()

    # Run the dashboard server directly
    import uvicorn
    import importlib.util
    spec = importlib.util.spec_from_file_location("dashboard_server", str(dashboard_script))
    if spec is None:
        console.print(f"[red]Could not load dashboard server from {dashboard_script}[/red]")
        raise typer.Exit(1)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    uvicorn.run(module.app, host=host, port=port, log_level="info")


@app.command("server")
def server(
    ctx: typer.Context,
    host: Annotated[
        str,
        typer.Option("--host", "-h", help="Host to bind to"),
    ] = "0.0.0.0",
    port: Annotated[
        int,
        typer.Option("--port", "-p", help="Port to bind to"),
    ] = 8000,
    log_level: Annotated[
        str,
        typer.Option("--log-level", "-l", help="Logging level"),
    ] = "INFO",
) -> None:
    """Start the SemeClaw event-driven server.

    Example:
        semeclaw server --host 0.0.0.0 --port 8080
    """
    logging.basicConfig(
        level=log_level.upper(),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    logger = logging.getLogger(__name__)
    workspace_dir = ctx.obj["workspace"]

    try:
        config = ctx.obj["config"]

        from semeclaw.core.agent_loader import AgentLoader
        from semeclaw.core.cron_loader import CronLoader
        from semeclaw.channel.base import Channel
        from semeclaw.server.server import Server

        agent_loader = AgentLoader(config)
        logger.info("Agent loader initialized")

        crons_path = workspace_dir / "crons"
        cron_loader = CronLoader(crons_path)
        logger.info(f"Cron loader initialized with {len(cron_loader)} crons")

        channels = Channel.from_config(config)
        if channels:
            logger.info(f"Initialized {len(channels)} channels: {list(channels.keys())}")

        pending_dir = workspace_dir / ".pending"

        srv = Server(
            config=config,
            agent_loader=agent_loader,
            cron_loader=cron_loader,
            channels=channels,
            pending_events_dir=str(pending_dir),
        )

        logger.info(f"Server starting on {host}:{port}")
        asyncio.run(srv.run(host=host, port=port))

    except Exception as e:
        console.print(f"[red]Error starting server:[/red] {e}")
        raise typer.Exit(1)


# ─────────────────────────────────────────────────────────────────────────
# v0.7.14+ — registry-driven commands (4 core agents + adapters + live demo)
# These delegate to the stdlib `cli/` package at the repo root so they remain
# usable via `python -m cli` even outside Typer.
# ─────────────────────────────────────────────────────────────────────────


def _bootstrap_repo_cli():
    """Make the repo-root `cli/` package importable from this module."""
    import sys as _sys
    from pathlib import Path as _P
    repo_root = _P(__file__).resolve().parents[3]   # .../SemeClaw
    if str(repo_root) not in _sys.path:
        _sys.path.insert(0, str(repo_root))


@app.command("agents")
def agents_cmd() -> None:
    """List every registered agent (5 core + 4 adapters) with model + tool info."""
    _bootstrap_repo_cli()
    from cli import agents as _a
    raise typer.Exit(_a.run())


@app.command("status")
def status_cmd() -> None:
    """Checklist: Python, deps, OpenRouter, Ollama, search backends, agents, demo."""
    _bootstrap_repo_cli()
    from cli import status as _s
    raise typer.Exit(_s.run())


@app.command("doctor")
def doctor_cmd() -> None:
    """End-to-end connectivity probe — DNS, dashboard, Supabase, OpenRouter, adapters."""
    _bootstrap_repo_cli()
    from cli import doctor as _d
    raise typer.Exit(_d.run())


@app.command("setup")
def setup_cmd() -> None:
    """Interactive onboarding: API key, smoke test, save ~/.semeclaw/env."""
    _bootstrap_repo_cli()
    from cli import setup as _su
    raise typer.Exit(_su.run())


@app.command("live-demo")
def live_demo_cmd() -> None:
    """Run the saved 4-agent live demo (Browser → Scraping → Research → Writer → Coder)."""
    _bootstrap_repo_cli()
    from cli import demo as _d
    raise typer.Exit(_d.run())


@app.command("tasks")
def tasks_cmd(
    args: Annotated[
        list[str] | None,
        typer.Argument(help="sync | list | dialog <id> | quota | gc"),
    ] = None,
) -> None:
    """Task ingest, dialog generation, retention. Hits a running War Room server."""
    _bootstrap_repo_cli()
    from cli import tasks as _t
    raise typer.Exit(_t.run(list(args or [])))


if __name__ == "__main__":
    app()
