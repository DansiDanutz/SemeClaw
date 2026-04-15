"""Built-in command implementations."""

from typing import TYPE_CHECKING

from semeclaw.core.commands.base import Command

if TYPE_CHECKING:
    from semeclaw.core.agent import AgentSession


class HelpCommand(Command):
    """Display help information about available commands."""

    name = "/help"
    aliases = ["/?"]
    description = "Display help information about available commands"

    async def execute(self, args: str, session: "AgentSession") -> str:
        """Execute help command.

        Args:
            args: Ignored
            session: Active agent session

        Returns:
            Formatted help text listing all commands
        """
        commands = session.command_registry.list_commands()

        if not commands:
            return "No commands registered."

        lines = ["Available commands:\n"]
        for cmd in commands:
            alias_str = f" (aliases: {', '.join(cmd.aliases)})" if cmd.aliases else ""
            desc = f" - {cmd.description}" if cmd.description else ""
            lines.append(f"  {cmd.name}{alias_str}{desc}")

        return "\n".join(lines)


class SkillsCommand(Command):
    """List available skills or show skill details."""

    name = "/skills"
    aliases = []
    description = "List available skills or show skill detail"

    async def execute(self, args: str, session: "AgentSession") -> str:
        """Execute skills command.

        Args:
            args: Optional skill name to show details
            session: Active agent session

        Returns:
            List of available skills or skill details
        """
        agent_def = session.agent.agent_def
        skills = agent_def.skills or []

        if not skills:
            return "No skills available."

        if args.strip():
            # Show detail for specific skill
            skill_name = args.strip()
            for skill in skills:
                if skill.name == skill_name:
                    lines = [f"Skill: {skill.name}"]
                    if skill.description:
                        lines.append(f"Description: {skill.description}")
                    return "\n".join(lines)
            return f"Skill '{skill_name}' not found."

        # List all skills
        lines = [f"Available skills ({len(skills)}):"]
        for skill in skills:
            lines.append(f"  - {skill.name}")
        return "\n".join(lines)


class SessionCommand(Command):
    """Display current session information."""

    name = "/session"
    aliases = []
    description = "Display session information"

    async def execute(self, args: str, session: "AgentSession") -> str:
        """Execute session command.

        Args:
            args: Ignored
            session: Active agent session

        Returns:
            Formatted session information
        """
        message_count = len(session.state.messages)
        lines = [
            "Session Information:",
            f"  ID: {session.session_id}",
            f"  Agent: {session.agent.agent_def.name}",
            f"  Messages: {message_count}",
            f"  Started: {session.started_at.isoformat()}",
        ]
        return "\n".join(lines)


class CompactCommand(Command):
    """Manually trigger message compaction."""

    name = "/compact"
    aliases = []
    description = "Manually trigger message compaction to reduce token usage"

    async def execute(self, args: str, session: "AgentSession") -> str:
        """Execute compact command.

        Args:
            args: Ignored
            session: Active agent session

        Returns:
            Compaction result message
        """
        from semeclaw.core.context_guard import ContextGuard

        context_guard = ContextGuard(session.agent.agent_def, session.agent.llm)

        before_tokens = context_guard.estimate_tokens(session.state)
        before_messages = len(session.state.messages)

        await context_guard.check_and_compact(session.state)

        after_tokens = context_guard.estimate_tokens(session.state)
        after_messages = len(session.state.messages)

        lines = [
            "Message Compaction Results:",
            f"  Tokens: {before_tokens} -> {after_tokens} (saved {before_tokens - after_tokens})",
            f"  Messages: {before_messages} -> {after_messages}",
        ]
        return "\n".join(lines)


class ContextCommand(Command):
    """Display current context window usage."""

    name = "/context"
    aliases = []
    description = "Show current token usage vs threshold"

    async def execute(self, args: str, session: "AgentSession") -> str:
        """Execute context command.

        Args:
            args: Ignored
            session: Active agent session

        Returns:
            Context usage information
        """
        from semeclaw.core.context_guard import ContextGuard

        context_guard = ContextGuard(session.agent.agent_def, session.agent.llm)
        current_tokens = context_guard.estimate_tokens(session.state)
        threshold = context_guard.threshold
        percentage = (current_tokens / threshold) * 100

        lines = [
            "Context Window Usage:",
            f"  Current: {current_tokens:,} tokens",
            f"  Threshold: {threshold:,} tokens",
            f"  Usage: {percentage:.1f}%",
        ]

        if current_tokens > threshold:
            lines.append("  Status: OVER THRESHOLD - Consider running /compact")
        elif current_tokens > threshold * 0.8:
            lines.append("  Status: WARNING - Approaching threshold")
        else:
            lines.append("  Status: OK")

        return "\n".join(lines)
