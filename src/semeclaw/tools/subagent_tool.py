"""Tool for inter-agent communication and delegation."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import TYPE_CHECKING, Any

from semeclaw.tools.base import BaseTool
from semeclaw.core.events import DispatchEvent, DispatchResultEvent, AgentEventSource

if TYPE_CHECKING:
    from semeclaw.core.eventbus import EventBus
    from semeclaw.core.agent import Agent, AgentSession
    from semeclaw.core.agent_loader import AgentLoader


class SubagentDispatchTool(BaseTool):
    """Tool for dispatching work to other agents."""

    def __init__(
        self,
        current_agent_id: str,
        eventbus: EventBus,
        agent_loader: "AgentLoader",
    ):
        """Initialize SubagentDispatchTool.

        Args:
            current_agent_id: ID of the agent making the dispatch
            eventbus: EventBus for publishing dispatch events
            agent_loader: AgentLoader for discovering available agents
        """
        self.current_agent_id = current_agent_id
        self.eventbus = eventbus
        self.agent_loader = agent_loader

        super().__init__(
            name="subagent_dispatch",
            description="Dispatch a task to another agent. Use this to delegate work to specialized agents like Cookie (memory manager).",
            parameters={
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "ID of the target agent (e.g., 'cookie' for memory operations)",
                    },
                    "task": {
                        "type": "string",
                        "description": "The task description for the target agent",
                    },
                    "context": {
                        "type": "string",
                        "description": "Optional context or additional information for the task",
                    },
                },
                "required": ["agent_id", "task"],
            },
        )

    async def execute(self, args: dict[str, Any], session: "AgentSession") -> str:
        """Execute the subagent_dispatch tool.

        Args:
            args: Tool arguments with 'agent_id', 'task', and optional 'context'
            session: Current AgentSession

        Returns:
            JSON result from the dispatched agent
        """
        agent_id = args.get("agent_id", "")
        task = args.get("task", "")
        context = args.get("context", "")

        if not agent_id or not task:
            return json.dumps(
                {"error": "agent_id and task are required"}
            )

        # Prevent self-dispatch
        if agent_id == self.current_agent_id:
            return json.dumps(
                {"error": f"Cannot dispatch to self (current agent: {self.current_agent_id})"}
            )

        # Verify agent exists
        try:
            target_agent_def = self.agent_loader.load(agent_id)
        except Exception as e:
            return json.dumps(
                {"error": f"Target agent '{agent_id}' not found: {str(e)}"}
            )

        # Create a new session for the dispatched agent
        child_session_id = str(uuid.uuid4())

        # Create and publish dispatch event
        dispatch_event = DispatchEvent(
            session_id=child_session_id,
            source=AgentEventSource(agent_id=self.current_agent_id),
            content=task,
            parent_session_id=session.session_id,
        )

        # Set up result listener
        result_future: asyncio.Future[DispatchResultEvent] = asyncio.Future()

        def result_handler(event: DispatchResultEvent) -> None:
            """Handle dispatch result."""
            if event.session_id == child_session_id and not result_future.done():
                result_future.set_result(event)

        self.eventbus.subscribe(DispatchResultEvent, result_handler)

        try:
            # Publish dispatch event
            await self.eventbus.publish(dispatch_event)

            # Wait for result with timeout
            result_event = await asyncio.wait_for(result_future, timeout=30.0)

            return json.dumps({
                "success": result_event.error is None,
                "result": result_event.content,
                "error": result_event.error,
                "session_id": child_session_id,
            })

        except asyncio.TimeoutError:
            return json.dumps({
                "success": False,
                "error": f"Dispatch to {agent_id} timed out after 30 seconds",
                "session_id": child_session_id,
            })
        except Exception as e:
            return json.dumps({
                "success": False,
                "error": f"Dispatch failed: {str(e)}",
                "session_id": child_session_id,
            })
        finally:
            # Clean up handler
            self.eventbus.unsubscribe(result_handler)


def create_subagent_dispatch_tool(
    current_agent_id: str,
    context: dict[str, Any],
) -> SubagentDispatchTool | None:
    """Factory function to create a subagent_dispatch tool.

    Args:
        current_agent_id: ID of the agent making the dispatch
        context: Context dictionary containing 'eventbus' and 'agent_loader'

    Returns:
        SubagentDispatchTool instance, or None if required context is missing
    """
    eventbus = context.get("eventbus")
    agent_loader = context.get("agent_loader")

    if not eventbus or not agent_loader:
        return None

    return SubagentDispatchTool(
        current_agent_id=current_agent_id,
        eventbus=eventbus,
        agent_loader=agent_loader,
    )
