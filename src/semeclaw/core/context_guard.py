"""Context window management and compaction."""

from typing import TYPE_CHECKING

import litellm
from litellm.types.completion import ChatCompletionMessageParam as Message

if TYPE_CHECKING:
    from semeclaw.core.agent_loader import AgentDef
    from semeclaw.core.session_state import SessionState
    from semeclaw.provider.llm import LLMProvider


class ContextGuard:
    """Manages context window size and performs message compaction when needed."""

    DEFAULT_THRESHOLD = 160000

    def __init__(
        self,
        agent_def: "AgentDef",
        llm_provider: "LLMProvider",
        threshold: int = DEFAULT_THRESHOLD,
    ) -> None:
        """Initialize context guard.

        Args:
            agent_def: Agent definition for system prompt
            llm_provider: LLM provider for token counting and summarization
            threshold: Token threshold for compaction (default 160000)
        """
        self.agent_def = agent_def
        self.llm_provider = llm_provider
        self.threshold = threshold

    def estimate_tokens(self, state: "SessionState") -> int:
        """Estimate total tokens in session state.

        Uses litellm.token_counter to estimate tokens for all messages
        including system prompt.

        Args:
            state: Current session state

        Returns:
            Estimated token count
        """
        messages = state.build_messages()
        try:
            tokens = litellm.token_counter(
                model=self.llm_provider.model,
                messages=messages,
            )
            return tokens
        except Exception:
            # Fallback: rough estimate (4 chars ~= 1 token)
            total_chars = sum(len(str(m.get("content", ""))) for m in messages)
            return total_chars // 4

    async def check_and_compact(self, state: "SessionState") -> None:
        """Check token usage and compact messages if over threshold.

        Strategy:
        1. Estimate tokens
        2. If under threshold, return
        3. First try: truncate large tool results (>10000 chars)
        4. If still over: summarize old messages using LLM

        Args:
            state: Session state to check and potentially compact
        """
        token_count = self.estimate_tokens(state)

        if token_count < self.threshold:
            return

        # Try truncating large tool results first
        self._truncate_large_tool_results(state.messages, max_chars=10000)

        # Re-estimate
        token_count = self.estimate_tokens(state)
        if token_count < self.threshold:
            return

        # If still over, summarize old messages
        await self._compact_messages(state)

    def _truncate_large_tool_results(
        self, messages: list[Message], max_chars: int = 10000
    ) -> None:
        """Truncate large tool results in messages.

        Args:
            messages: Message list to modify in-place
            max_chars: Maximum characters per tool result
        """
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str) and len(content) > max_chars:
                # Truncate with ellipsis
                msg["content"] = content[:max_chars] + "\n... (truncated)"

    async def _compact_messages(self, state: "SessionState") -> None:
        """Summarize old messages to reduce token count.

        Keeps 80% of recent messages and summarizes oldest 50%.

        Args:
            state: Session state to compact
        """
        messages = state.messages
        if len(messages) < 4:
            return

        # Keep 80% most recent, summarize oldest 50%
        keep_count = max(2, int(len(messages) * 0.8))
        summarize_count = len(messages) - keep_count

        if summarize_count < 2:
            return

        # Messages to summarize (oldest 50% of non-kept messages)
        to_summarize = messages[: summarize_count // 2]

        if not to_summarize:
            return

        # Build context for summarization
        summary_prompt = "Summarize the following conversation in 2-3 sentences:\n\n"
        for msg in to_summarize:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            summary_prompt += f"{role}: {content}\n"

        # Use LLM to create summary
        summary_messages: list[Message] = [
            {"role": "user", "content": summary_prompt}
        ]

        try:
            summary = await self.llm_provider.chat(summary_messages)

            # Replace old messages with summary
            summary_msg: Message = {
                "role": "system",
                "content": f"[Summary of earlier conversation]: {summary}",
            }

            # Remove old messages and insert summary at beginning
            del state.messages[: summarize_count // 2]
            state.messages.insert(0, summary_msg)

        except Exception:
            # If summarization fails, just truncate messages
            del state.messages[: summarize_count // 2]
