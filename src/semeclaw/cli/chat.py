"""Chat CLI command for interactive sessions."""

import asyncio

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text

from semeclaw.core.agent import Agent
from semeclaw.core.agent_loader import AgentLoader
from semeclaw.utils.config import Config


class ChatLoop:
    """Interactive chat session with SemeClaw."""

    def __init__(self, config: Config, agent_id: str | None = None):
        self.config = config
        self.console = Console()

        # Load agent
        loader = AgentLoader(config)
        agent_id = agent_id or config.default_agent
        self.agent_def = loader.load(agent_id)

        # Create agent and session
        self.agent = Agent(self.agent_def, config)
        self.session = self.agent.new_session()

    def get_user_input(self) -> str:
        """Get user input with styled prompt."""
        prompt_text = Text("You", style="cyan")
        user_input = Prompt.ask(prompt_text, console=self.console)
        return user_input.strip()

    def display_agent_response(self, content: str) -> None:
        """Display agent response with styled prefix."""
        prefix = Text(f"{self.agent_def.id}: ", style="green")
        self.console.print(prefix, end="")
        self.console.print(content)

    async def run(self) -> None:
        """Run the interactive chat loop."""
        self.console.print(
            Panel(
                Text("Welcome to SemeClaw - DansLab AI Brain!", style="bold cyan"),
                title="SemeClaw",
                border_style="cyan",
            )
        )
        self.console.print("Type 'quit' or 'exit' to end the session.\n")

        try:
            while True:
                user_input = await asyncio.to_thread(self.get_user_input)

                if user_input.lower() in ("quit", "exit", "q"):
                    self.console.print("\n[bold yellow]See you later, DansLab![/bold yellow]")
                    break

                if not user_input:
                    continue

                try:
                    response = await self.session.chat(user_input)
                    self.display_agent_response(response)
                except Exception as e:
                    self.console.print(f"\n[bold red]Error:[/bold red] {e}\n")

        except (KeyboardInterrupt, EOFError):
            self.console.print("\n[bold yellow]See you later, DansLab![/bold yellow]")


def chat_command(ctx: typer.Context, agent_id: str | None = None) -> None:
    """Start interactive chat session."""
    config = ctx.obj.get("config")
    chat_loop = ChatLoop(config, agent_id=agent_id)
    asyncio.run(chat_loop.run())
