"""CLI command to start the SemeClaw server."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import click

from semeclaw.channel.base import Channel
from semeclaw.core.agent_loader import AgentLoader
from semeclaw.core.cron_loader import CronLoader
from semeclaw.server.server import Server
from semeclaw.utils.config import Config

logger = logging.getLogger(__name__)


@click.command(name="server")
@click.option(
    "--workspace",
    "-w",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    default=".",
    help="Path to the SemeClaw workspace.",
)
@click.option(
    "--host",
    "-h",
    default="0.0.0.0",
    help="Host to bind to.",
)
@click.option(
    "--port",
    "-p",
    type=int,
    default=8000,
    help="Port to bind to.",
)
@click.option(
    "--log-level",
    "-l",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
    default="INFO",
    help="Logging level.",
)
def start_server(
    workspace: str,
    host: str,
    port: int,
    log_level: str,
) -> None:
    """Start the SemeClaw server.

    Starts the event-driven server with all workers and the FastAPI application.

    Example:
        semeclaw server --workspace /path/to/workspace --port 8080
    """
    # Configure logging
    logging.basicConfig(
        level=log_level.upper(),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    workspace_dir = Path(workspace).resolve()
    logger.info(f"Starting SemeClaw Server from workspace: {workspace_dir}")

    try:
        # Load configuration
        config = Config.load(workspace_dir)
        logger.info("Configuration loaded")

        # Initialize components
        agent_loader = AgentLoader(config.agents_path)
        logger.info(f"Agent loader initialized with {len(agent_loader)} agents")

        # Initialize cron loader
        crons_path = workspace_dir / "crons"
        cron_loader = CronLoader(crons_path)
        logger.info(f"Cron loader initialized with {len(cron_loader)} crons")

        # Initialize channels
        channels = Channel.from_config(config)
        if channels:
            logger.info(f"Initialized {len(channels)} channels: {list(channels.keys())}")
        else:
            logger.warning("No channels configured")

        # Set up pending events directory
        pending_dir = workspace_dir / ".pending"
        logger.info(f"Using pending events directory: {pending_dir}")

        # Create and run server
        server = Server(
            config=config,
            agent_loader=agent_loader,
            cron_loader=cron_loader,
            channels=channels,
            pending_events_dir=str(pending_dir),
        )

        logger.info(f"Server starting on {host}:{port}")
        asyncio.run(server.run(host=host, port=port))

    except FileNotFoundError as e:
        logger.error(f"Configuration file not found: {e}")
        raise click.ClickException(str(e))
    except Exception as e:
        logger.exception(f"Failed to start server: {e}")
        raise click.ClickException(str(e))


if __name__ == "__main__":
    start_server()
