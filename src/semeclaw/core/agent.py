"""Agent and AgentSession for SemeClaw."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from litellm.types.completion import ChatCompletionMessageParam as Message

from semeclaw.provider.llm import LLMProvider
from semeclaw.core.session_state import SessionState
from semeclaw.core.commands import CommandRegistry
from semeclaw.tools import ToolRegistry

if TYPE_CHECKING:
    from semeclaw.core.agent_loader import AgentDef
    from semeclaw.utils.config import Config
    from semeclaw.core.history import HistoryStore


class Agent:
    """A configured agent that creates and manages conversation sessions."""

    def __init__(self, agent_def: "AgentDef", config: "Config") -> None:
        self.agent_def = agent_def
        self.config = config
        self.llm = LLMProvider.from_config(agent_def.llm)
        self.tool_registry = self._build_tools()
        self.history_store: HistoryStore | None = None

    def _build_tools(self) -> ToolRegistry:
        """Build tool registry for this agent."""
        registry = ToolRegistry.with_builtins()

        # Add skill tool if skills are enabled
        if hasattr(self.agent_def, "allow_skills") and self.agent_def.allow_skills:
            from semeclaw.tools.skill_tool import create_skill_tool

            skill_tool = create_skill_tool(self.config)
            registry.register(skill_tool)

        return registry

    def new_session(self, session_id: str | None = None) -> "AgentSession":
        """Create a new conversation session."""
        session_id = session_id or str(uuid.uuid4())

        state = SessionState(
            session_id=session_id,
            agent=self,
            messages=[],
            history_store=self.history_store,
        )

        session = AgentSession(agent=self, state=state)

        # Track session in history if available
        if self.history_store:
            self.history_store.create_session(
                session_id=session_id,
                agent_id=self.agent_def.id,
                source="cli",
            )

        return session


@dataclass
class AgentSession:
    """Chat orchestrator - operates on swappable SessionState."""

    agent: Agent
    state: SessionState
    started_at: datetime = field(default_factory=datetime.now)
    command_registry: CommandRegistry = field(default_factory=CommandRegistry.with_builtins)

    @property
    def session_id(self) -> str:
        """Delegate to state."""
        return self.state.session_id

    async def chat(self, message: str) -> str:
        """Send a message to the LLM and get a response.

        Checks if message starts with "/" and dispatches as command first.
        If no command matches, proceeds with LLM chat with tool support.
        """
        # Check for slash command
        command_output = await self.command_registry.dispatch(message, self)
        if command_output is not None:
            return command_output

        # Process as normal chat
        user_msg: Message = {"role": "user", "content": message}
        self.state.add_message(user_msg)

        # Tool loop: keep calling LLM and executing tools until no more tool calls
        tool_schemas = self.agent.tool_registry.get_tool_schemas()
        while True:
            messages = self.state.build_messages()
            response, tool_calls = await self.agent.llm.chat(
                messages, tool_schemas=tool_schemas if tool_schemas else None
            )

            # If we got text response, add it and break
            if response:
                assistant_msg: Message = {"role": "assistant", "content": response}
                self.state.add_message(assistant_msg)

            # Execute tool calls if any
            if tool_calls:
                for tool_call in tool_calls:
                    tool = self.agent.tool_registry.get(tool_call.name)
                    if tool:
                        tool_result = await tool.execute(tool_call.arguments, self)
                        tool_result_msg: Message = {
                            "role": "tool",
                            "content": tool_result,
                            "tool_call_id": tool_call.id,
                            "name": tool_call.name,
                        }
                        self.state.add_message(tool_result_msg)
                    else:
                        error_msg: Message = {
                            "role": "tool",
                            "content": f"Tool not found: {tool_call.name}",
                            "tool_call_id": tool_call.id,
                            "name": tool_call.name,
                        }
                        self.state.add_message(error_msg)
            else:
                # No more tool calls, exit loop
                break

        return response
