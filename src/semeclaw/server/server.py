"""Main server class for SemeClaw."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from semeclaw.core.context import SharedContext
from semeclaw.core.eventbus import EventBus
from semeclaw.core.routing import RoutingTable
from semeclaw.server.agent_worker import AgentWorker
from semeclaw.server.app import create_app
from semeclaw.server.channel_worker import ChannelWorker
from semeclaw.server.cron_worker import CronWorker
from semeclaw.server.delivery_worker import DeliveryWorker
from semeclaw.server.websocket_worker import WebSocketWorker
from semeclaw.server.worker import Worker

if TYPE_CHECKING:
    from semeclaw.channel.base import Channel
    from semeclaw.core.agent_loader import AgentLoader
    from semeclaw.core.cron_loader import CronLoader
    from semeclaw.utils.config import Config


logger = logging.getLogger(__name__)


class Server:
    """Main SemeClaw server orchestrating all workers."""

    def __init__(
        self,
        config: Config,
        agent_loader: AgentLoader,
        cron_loader: CronLoader,
        channels: dict[str, Channel] | None = None,
        pending_events_dir: str | None = None,
    ):
        """Initialize the server.

        Args:
            config: The configuration.
            agent_loader: The agent loader.
            cron_loader: The cron loader.
            channels: Optional dictionary of channels.
            pending_events_dir: Optional directory for pending event persistence.
        """
        self.config = config
        self.agent_loader = agent_loader
        self.cron_loader = cron_loader
        self.channels = channels or {}
        self.logger = logging.getLogger(__name__)

        # Initialize core components
        from pathlib import Path

        pending_dir = Path(pending_events_dir) if pending_events_dir else None
        self.eventbus = EventBus(pending_dir=pending_dir)

        # Create shared context
        self.context = SharedContext(
            config=config,
            agent_loader=agent_loader,
            cron_loader=cron_loader,
            eventbus=self.eventbus,
            channels=self.channels,
        )

        # Initialize routing table
        self.routing_table = RoutingTable()
        self.context.set_routing_table(self.routing_table)

        # Initialize workers
        self.workers: list[Worker] = []
        self._setup_workers()

        # Create FastAPI app
        self.app = create_app(self.context)

    def _setup_workers(self) -> None:
        """Set up all server workers."""
        # Agent worker - processes inbound and dispatch events
        agent_worker = AgentWorker(self.eventbus, self.agent_loader, self.config)
        self.workers.append(agent_worker)
        self.logger.info("Agent worker initialized")

        # Channel worker - manages platform integrations
        if self.channels:
            channel_worker = ChannelWorker(self.eventbus, self.channels)
            self.workers.append(channel_worker)
            self.logger.info(f"Channel worker initialized ({len(self.channels)} channels)")

        # Delivery worker - sends outbound events to platforms
        if self.channels:
            delivery_worker = DeliveryWorker(self.eventbus, self.channels)
            self.workers.append(delivery_worker)
            self.logger.info("Delivery worker initialized")

        # WebSocket worker - manages real-time connections
        websocket_worker = WebSocketWorker(self.eventbus)
        self.workers.append(websocket_worker)
        self.context.set_websocket_worker(websocket_worker)
        self.logger.info("WebSocket worker initialized")

        # Cron worker - manages scheduled jobs
        cron_worker = CronWorker(self.eventbus, self.cron_loader)
        self.workers.append(cron_worker)
        self.logger.info("Cron worker initialized")

    async def run(self, host: str = "0.0.0.0", port: int = 8000) -> None:
        """Run the server.

        This starts the event bus, all workers, and the FastAPI server.

        Args:
            host: Host to bind to.
            port: Port to bind to.
        """
        import uvicorn

        self.logger.info("SemeClaw Server starting")
        self.logger.info(f"Context: {self.context}")

        try:
            # Create tasks for event bus and workers
            tasks = [
                asyncio.create_task(self.eventbus.run(), name="eventbus"),
            ]

            for worker in self.workers:
                tasks.append(asyncio.create_task(worker.start(), name=worker.__class__.__name__))

            # Create a task for the FastAPI server
            uvi_config = uvicorn.Config(
                self.app,
                host=host,
                port=port,
                log_level="info",
            )
            uvi_server = uvicorn.Server(uvi_config)

            # Run the uvicorn server as a task
            await asyncio.create_task(uvi_server.serve())

            # Wait for all tasks (should block indefinitely)
            await asyncio.gather(*tasks)

        except KeyboardInterrupt:
            self.logger.info("Interrupt received, shutting down")
        except Exception as e:
            self.logger.exception(f"Server error: {e}")
            raise
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        """Shut down the server gracefully."""
        self.logger.info("Shutting down SemeClaw Server")

        # Stop the event bus
        # Note: In production, this would be coordinated through asyncio cancellation

        # Stop all workers
        for worker in self.workers:
            await worker.stop()

        self.logger.info("Server shutdown complete")

    def add_routing_binding(
        self,
        agent_id: str,
        pattern: str | None = None,
        tier: int = 0,
    ) -> None:
        """Add a routing binding.

        Args:
            agent_id: The target agent ID.
            pattern: Optional regex pattern to match sources.
            tier: Priority tier (higher = higher priority).
        """
        self.routing_table.add_binding(agent_id, pattern, tier)
        self.logger.info(f"Added routing binding: {agent_id}")

    def get_context(self) -> SharedContext:
        """Get the shared context.

        Returns:
            The shared context.
        """
        return self.context
