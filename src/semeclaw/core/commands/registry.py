"""Command registry for managing slash commands."""

from typing import TYPE_CHECKING

from semeclaw.core.commands.base import Command

if TYPE_CHECKING:
    from semeclaw.core.agent import AgentSession


class CommandRegistry:
    """Registry for managing slash commands."""

    def __init__(self) -> None:
        """Initialize the command registry."""
        self._commands: dict[str, Command] = {}

    def register(self, cmd: Command) -> None:
        """Register a command by name and aliases.

        Args:
            cmd: Command instance to register
        """
        self._commands[cmd.name] = cmd
        for alias in cmd.aliases:
            self._commands[alias] = cmd

    def resolve(self, input_text: str) -> tuple[Command, str] | None:
        """Resolve command from input text.

        Parses "/command args" format and returns command with arguments.

        Args:
            input_text: User input, e.g. "/help" or "/skills detail"

        Returns:
            Tuple of (Command, args) if resolved, None otherwise
        """
        if not input_text.startswith("/"):
            return None

        parts = input_text.split(None, 1)
        command_name = parts[0]
        args = parts[1] if len(parts) > 1 else ""

        cmd = self._commands.get(command_name)
        if cmd is None:
            return None

        return (cmd, args)

    async def dispatch(self, input_text: str, session: "AgentSession") -> str | None:
        """Dispatch a command if input matches registered commands.

        Args:
            input_text: User input
            session: Active agent session

        Returns:
            Command output if command was found and executed, None otherwise
        """
        resolved = self.resolve(input_text)
        if resolved is None:
            return None

        cmd, args = resolved
        return await cmd.execute(args, session)

    def list_commands(self) -> list[Command]:
        """Get all unique registered commands.

        Returns:
            List of unique command instances
        """
        seen = set()
        commands = []
        for cmd in self._commands.values():
            if cmd.name not in seen:
                commands.append(cmd)
                seen.add(cmd.name)
        return commands

    @classmethod
    def with_builtins(cls) -> "CommandRegistry":
        """Create a registry with built-in commands.

        Returns:
            CommandRegistry with HelpCommand, SkillsCommand, and SessionCommand
        """
        from semeclaw.core.commands.handlers import (
            ContextCommand,
            CompactCommand,
            GitHubPrCommand,
            HelpCommand,
            PiCompanyCommand,
            PluginsCommand,
            ReviewLoopCommand,
            SkillsCommand,
            SessionCommand,
        )

        registry = cls()
        registry.register(HelpCommand())
        registry.register(SkillsCommand())
        registry.register(SessionCommand())
        registry.register(CompactCommand())
        registry.register(ContextCommand())
        registry.register(PiCompanyCommand())
        registry.register(GitHubPrCommand())
        registry.register(ReviewLoopCommand())
        registry.register(PluginsCommand())
        return registry
