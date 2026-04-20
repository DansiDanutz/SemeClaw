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

    dashboard_script = Path(__file__).parent.parent.parent.parent / "war_room" / "dashboard" / "server.py"
    if not dashboard_script.exists():
        # Try relative to install
        dashboard_script = Path(sys.prefix) / "war_room" / "dashboard" / "server.py"

    console.print(f"[bold cyan]🚀 Starting War Room dashboard on {host}:{port}...[/bold cyan]")
    console.print(f"[dim]Dashboard: http://{host}:{port}[/dim]")
    console.print()

    # Run the dashboard server directly
    import uvicorn
    import importlib.util
    spec = importlib.util.spec_from_file_location("dashboard_server", str(dashboard_script))
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


if __name__ == "__main__":
    app()
