"""CLI interface for SemeClaw using Typer."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from semeclaw.cli.chat import chat_command
from semeclaw.utils.config import Config

app = typer.Typer(
    name="semeclaw",
    help="SemeClaw: The AI Brain of DansLab Company",
    no_args_is_help=True,
    add_completion=True,
)

console = Console()


def workspace_callback(ctx: typer.Context, workspace: str) -> Path:
    """Store workspace path in context for later use."""
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
    workspace_path = ctx.obj["workspace"]
    config_file = workspace_path / "config.user.yaml"

    if not config_file.exists():
        console.print(f"[yellow]No configuration found at {config_file}[/yellow]")
        raise typer.Exit(1)

    try:
        cfg = Config.load(workspace_path)
        ctx.obj["config"] = cfg
    except Exception as e:
        console.print(f"[red]Error loading config: {e}[/red]")
        raise typer.Exit(1)


@app.command("chat")
def chat(
    ctx: typer.Context,
    agent: Annotated[
        str | None,
        typer.Option(
            "--agent",
            "-a",
            help="Agent ID to use (overrides default_agent from config)",
        ),
    ] = None,
) -> None:
    """Start interactive chat session with SemeClaw."""
    chat_command(ctx, agent_id=agent)


@app.command("server")
def server(
    ctx: typer.Context,
    host: Annotated[
        str,
        typer.Option(
            "--host",
            "-h",
            help="Host to bind to",
        ),
    ] = "0.0.0.0",
    port: Annotated[
        int,
        typer.Option(
            "--port",
            "-p",
            help="Port to bind to",
        ),
    ] = 8000,
    log_level: Annotated[
        str,
        typer.Option(
            "--log-level",
            "-l",
            help="Logging level",
        ),
    ] = "INFO",
) -> None:
    """Start the SemeClaw event-driven server.

    Starts the server with all workers and the FastAPI application.

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

        # Initialize components
        agent_loader = AgentLoader(config)
        logger.info("Agent loader initialized")

        crons_path = workspace_dir / "crons"
        cron_loader = CronLoader(crons_path)
        logger.info(f"Cron loader initialized with {len(cron_loader)} crons")

        channels = Channel.from_config(config)
        if channels:
            logger.info(f"Initialized {len(channels)} channels: {list(channels.keys())}")

        pending_dir = workspace_dir / ".pending"

        # Create and run server
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
        console.print(f"[red]Error starting server: {e}[/red]")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
