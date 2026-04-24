"""Tool for agents to proactively send messages via the event bus."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from semeclaw.core.events import EventSource, OutboundEvent
from semeclaw.tools.base import BaseTool

if TYPE_CHECKING:
    from semeclaw.core.agent import AgentSession
    from semeclaw.core.eventbus import EventBus


class PostMessageTool(BaseTool):
    """Tool allowing agents to post messages to external platforms."""

    def __init__(self, eventbus: EventBus):
        """Initialize PostMessageTool.

        Args:
            eventbus: EventBus instance for publishing messages
        """
        self.eventbus = eventbus

        super().__init__(
            name="post_message",
            description="Send a message to the user or external platform. Only available in cron or dispatch context.",
            parameters={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The message content to send",
                    },
                },
                "required": ["content"],
            },
        )

    async def execute(self, args: dict[str, Any], session: AgentSession) -> str:
        """Execute the post_message tool.

        Args:
            args: Tool arguments with 'content' key
            session: AgentSession for context

        Returns:
            Status message
        """
        content = args.get("content", "")
        if not content:
            return "Error: content cannot be empty"

        # Create outbound event with the session's source
        event = OutboundEvent(
            session_id=session.session_id,
            source=session.state.source if hasattr(session.state, "source") else EventSource(),
            content=content,
        )

        # Publish to event bus
        await self.eventbus.publish(event)

        return "Message queued for delivery"


def create_post_message_tool(context: dict[str, Any]) -> PostMessageTool | None:
    """Factory function to create a post_message tool.

    Args:
        context: Context dictionary containing 'eventbus' and 'source'

    Returns:
        PostMessageTool instance, or None if not in cron/dispatch context
    """
    eventbus = context.get("eventbus")
    source = context.get("source")

    if not eventbus:
        return None

    # Only available in cron or agent dispatch context
    if source and not (source.is_cron or source.is_agent):
        return None

    return PostMessageTool(eventbus)
