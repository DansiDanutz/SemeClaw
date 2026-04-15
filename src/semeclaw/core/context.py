"""Shared context for the SemeClaw system."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from semeclaw.utils.config import Config
    from semeclaw.provider.history import HistoryStore
    from semeclaw.core.agent_loader import AgentLoader
    from semeclaw.core.commands import CommandRegistry
    from semeclaw.core.routing import RoutingTable
    from semeclaw.core.cron_loader import CronLoader
    from semeclaw.core.eventbus import EventBus
    from semeclaw.server.websocket_worker import WebSocketWorker
    from semeclaw.channel.base import Channel


class SharedContext:
    """Central context holding shared system components."""

    def __init__(
        self,
        config: Config,
        history_store: HistoryStore | None = None,
        agent_loader: AgentLoader | None = None,
        command_registry: CommandRegistry | None = None,
        routing_table: RoutingTable | None = None,
        cron_loader: CronLoader | None = None,
        eventbus: EventBus | None = None,
        websocket_worker: WebSocketWorker | None = None,
        channels: dict[str, Channel] | None = None,
    ):
        """Initialize the shared context.

        Args:
            config: The configuration object.
            history_store: Optional history store for conversation persistence.
            agent_loader: Optional agent loader.
            command_registry: Optional command registry.
            routing_table: Optional routing table.
            cron_loader: Optional cron loader.
            eventbus: Optional event bus.
            websocket_worker: Optional WebSocket worker.
            channels: Optional dictionary of channels.
        """
        self.config = config
        self.history_store = history_store
        self.agent_loader = agent_loader
        self.command_registry = command_registry
        self.routing_table = routing_table
        self.cron_loader = cron_loader
        self.eventbus = eventbus
        self.websocket_worker = websocket_worker
        self.channels = channels or {}
        self.logger = logging.getLogger(__name__)

    def set_history_store(self, history_store: HistoryStore) -> None:
        """Set the history store.

        Args:
            history_store: The history store instance.
        """
        self.history_store = history_store
        self.logger.debug("History store set in shared context")

    def set_agent_loader(self, agent_loader: AgentLoader) -> None:
        """Set the agent loader.

        Args:
            agent_loader: The agent loader instance.
        """
        self.agent_loader = agent_loader
        self.logger.debug("Agent loader set in shared context")

    def set_command_registry(self, command_registry: CommandRegistry) -> None:
        """Set the command registry.

        Args:
            command_registry: The command registry instance.
        """
        self.command_registry = command_registry
        self.logger.debug("Command registry set in shared context")

    def set_routing_table(self, routing_table: RoutingTable) -> None:
        """Set the routing table.

        Args:
            routing_table: The routing table instance.
        """
        self.routing_table = routing_table
        self.logger.debug("Routing table set in shared context")

    def set_cron_loader(self, cron_loader: CronLoader) -> None:
        """Set the cron loader.

        Args:
            cron_loader: The cron loader instance.
        """
        self.cron_loader = cron_loader
        self.logger.debug("Cron loader set in shared context")

    def set_eventbus(self, eventbus: EventBus) -> None:
        """Set the event bus.

        Args:
            eventbus: The event bus instance.
        """
        self.eventbus = eventbus
        self.logger.debug("Event bus set in shared context")

    def set_websocket_worker(self, websocket_worker: WebSocketWorker) -> None:
        """Set the WebSocket worker.

        Args:
            websocket_worker: The WebSocket worker instance.
        """
        self.websocket_worker = websocket_worker
        self.logger.debug("WebSocket worker set in shared context")

    def set_channels(self, channels: dict[str, Channel]) -> None:
        """Set the channels.

        Args:
            channels: Dictionary of platform_name -> Channel.
        """
        self.channels = channels
        self.logger.debug(f"Set {len(channels)} channels in shared context")

    def verify_complete(self) -> bool:
        """Verify that all essential components are initialized.

        Returns:
            True if all essential components are set.
        """
        required = ["config", "agent_loader", "eventbus"]
        missing = [name for name in required if not getattr(self, name)]

        if missing:
            self.logger.warning(f"Missing required context components: {missing}")
            return False

        return True

    def __repr__(self) -> str:
        """Get string representation."""
        components = []
        if self.config:
            components.append("config")
        if self.history_store:
            components.append("history_store")
        if self.agent_loader:
            components.append("agent_loader")
        if self.command_registry:
            components.append("command_registry")
        if self.routing_table:
            components.append("routing_table")
        if self.cron_loader:
            components.append("cron_loader")
        if self.eventbus:
            components.append("eventbus")
        if self.websocket_worker:
            components.append("websocket_worker")
        if self.channels:
            components.append(f"channels({len(self.channels)})")

        return f"SharedContext({', '.join(components)})"
