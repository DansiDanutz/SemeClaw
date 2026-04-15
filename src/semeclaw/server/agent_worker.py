"""Agent worker for processing events with agent sessions."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from semeclaw.server.worker import SubscriberWorker
from semeclaw.core.events import (
    InboundEvent,
    DispatchEvent,
    OutboundEvent,
    DispatchResultEvent,
)
from semeclaw.core.agent import Agent

if TYPE_CHECKING:
    from semeclaw.core.eventbus import EventBus
    from semeclaw.core.agent_loader import AgentLoader
    from semeclaw.utils.config import Config


class AgentWorker(SubscriberWorker):
    """Worker that runs agent sessions in response to events."""

    def __init__(
        self,
        eventbus: EventBus,
        agent_loader: AgentLoader,
        config: Config,
    ):
        """Initialize the agent worker.

        Args:
            eventbus: The event bus.
            agent_loader: Agent loader for instantiating agents.
            config: The configuration.
        """
        super().__init__(eventbus)
        self.agent_loader = agent_loader
        self.config = config
        self.logger = logging.getLogger(__name__)

    async def run(self) -> None:
        """Run the agent worker.

        Subscribes to InboundEvent and DispatchEvent and processes them.
        """
        self.subscribe(InboundEvent, self._handle_inbound)
        self.subscribe(DispatchEvent, self._handle_dispatch)

        # Keep the worker running
        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise

    async def _handle_inbound(self, event: InboundEvent) -> None:
        """Handle an inbound event from an external platform.

        Args:
            event: The inbound event.
        """
        try:
            self.logger.info(
                f"Processing inbound event (session={event.session_id}, "
                f"source={event.source})"
            )

            # Load agent — cron events may specify a target_agent
            target = getattr(event, "target_agent", None) or self.config.default_agent
            agent_def = self.agent_loader.load(target)
            agent = Agent(agent_def, self.config)
            session = agent.new_session(event.session_id)

            # Run the chat
            response = await session.chat(event.content)

            # Emit the response
            await self._emit_response(event, response)
        except Exception as e:
            self.logger.exception(f"Error processing inbound event: {e}")
            await self._emit_error(event, str(e))

    async def _handle_dispatch(self, event: DispatchEvent) -> None:
        """Handle a dispatch event from another agent.

        Args:
            event: The dispatch event.
        """
        try:
            self.logger.info(
                f"Processing dispatch event (session={event.session_id}, "
                f"parent={event.parent_session_id})"
            )

            # Load the agent to dispatch to (from the source agent_id if available)
            agent_id = event.source.agent_id if event.source.is_agent else self.config.default_agent
            agent_def = self.agent_loader.load(agent_id)
            agent = Agent(agent_def, self.config)
            session = agent.new_session(event.session_id)

            # Run the chat
            response = await session.chat(event.content)

            # Emit the result
            await self._emit_dispatch_result(event, response)
        except Exception as e:
            self.logger.exception(f"Error processing dispatch event: {e}")
            await self._emit_dispatch_error(event, str(e))

    async def _emit_response(self, source_event: InboundEvent, response: str) -> None:
        """Emit a response to an inbound event.

        Args:
            source_event: The inbound event.
            response: The response content.
        """
        event = OutboundEvent(
            session_id=source_event.session_id,
            source=source_event.source,
            content=response,
        )
        await self.publish(event)
        self.logger.debug(f"Emitted response for session={source_event.session_id}")

    async def _emit_error(self, source_event: InboundEvent, error: str) -> None:
        """Emit an error response to an inbound event.

        Args:
            source_event: The inbound event.
            error: The error message.
        """
        event = OutboundEvent(
            session_id=source_event.session_id,
            source=source_event.source,
            content=f"Error: {error}",
            error=error,
        )
        await self.publish(event)
        self.logger.warning(f"Emitted error for session={source_event.session_id}: {error}")

    async def _emit_dispatch_result(
        self, source_event: DispatchEvent, response: str
    ) -> None:
        """Emit a result to a dispatch event.

        Args:
            source_event: The dispatch event.
            response: The response content.
        """
        event = DispatchResultEvent(
            session_id=source_event.session_id,
            source=source_event.source,
            content=response,
        )
        await self.publish(event)
        self.logger.debug(f"Emitted dispatch result for session={source_event.session_id}")

    async def _emit_dispatch_error(self, source_event: DispatchEvent, error: str) -> None:
        """Emit an error result to a dispatch event.

        Args:
            source_event: The dispatch event.
            error: The error message.
        """
        event = DispatchResultEvent(
            session_id=source_event.session_id,
            source=source_event.source,
            content=f"Error: {error}",
            error=error,
        )
        await self.publish(event)
        self.logger.warning(f"Emitted dispatch error for session={source_event.session_id}: {error}")
