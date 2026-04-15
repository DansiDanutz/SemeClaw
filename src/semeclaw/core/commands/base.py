"""Base command abstraction for SemeClaw."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from semeclaw.core.agent import AgentSession


class Command(ABC):
    """Abstract base class for slash commands."""

    name: str
    aliases: list[str] = []
    description: str = ""

    @abstractmethod
    async def execute(self, args: str, session: "AgentSession") -> str:
        """Execute the command with given arguments.

        Args:
            args: Command arguments (everything after command name)
            session: The active agent session

        Returns:
            Command output as string
        """
        pass
