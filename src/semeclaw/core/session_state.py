"""Session state management."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from litellm.types.completion import ChatCompletionMessageParam as Message

if TYPE_CHECKING:
    from semeclaw.core.agent import Agent
    from semeclaw.core.history import HistoryStore


@dataclass
class SessionState:
    """Pure conversation state container."""

    session_id: str
    agent: "Agent"
    messages: list[Message]
    history_store: HistoryStore | None = field(default=None, repr=False)

    def add_message(self, message: Message) -> None:
        """Add message to conversation history."""
        self.messages.append(message)
        if self.history_store:
            self.history_store.save_message(self.session_id, message)

    def build_messages(self) -> list[Message]:
        """Build messages list with system prompt prepended."""
        system_prompt = self.agent.agent_def.agent_md
        messages: list[Message] = [{"role": "system", "content": system_prompt}]
        messages.extend(self.messages)
        return messages
