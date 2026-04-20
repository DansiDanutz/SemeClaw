"""Built-in command implementations."""

from pathlib import Path
from typing import TYPE_CHECKING

from semeclaw.core.commands.base import Command
from semeclaw.core.plugin_loader import PluginLoader
from semeclaw.integrations.pi_company import (
    bootstrap_repo,
    format_bootstrap_result,
    format_status,
    get_status,
)
from semeclaw.integrations.review_loop import (
    DEFAULT_REVIEW_LOOP_SCHEDULE,
    format_review_loop_install,
    install_review_loop,
)
from semeclaw.integrations.github_pr import (
    format_local_review_bundle_result,
    format_pr_status,
    format_review_bundle_result,
    get_pr_status,
    write_review_bundle_or_local,
)

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


class PiCompanyCommand(Command):
    """Inspect or bootstrap pi-company in a target repository."""

    name = "/pi-company"
    aliases = []
    description = "Inspect or bootstrap pi-company in a repository"

    async def execute(self, args: str, session: "AgentSession") -> str:
        parts = args.split()
        subcommand = parts[0] if parts else "status"
        target = Path(parts[1]).expanduser() if len(parts) > 1 else session.agent.config.workspace

        if subcommand == "status":
            status = get_status(target_path=target, workspace=session.agent.config.workspace)
            return format_status(status)

        if subcommand == "bootstrap":
            company_name = " ".join(parts[2:]).strip() or None
            try:
                result = bootstrap_repo(
                    target_path=target,
                    workspace=session.agent.config.workspace,
                    company_name=company_name,
                )
            except Exception as exc:
                return f"Error bootstrapping pi-company: {exc}"
            return format_bootstrap_result(result)

        return (
            "Usage:\n"
            "  /pi-company status [target_path]\n"
            "  /pi-company bootstrap [target_path] [company_name]"
        )


class GitHubPrCommand(Command):
    """Inspect or bundle a GitHub pull request with gh."""

    name = "/github-pr"
    aliases = ["/pr"]
    description = "Inspect PR status or generate a local review bundle"

    async def execute(self, args: str, session: "AgentSession") -> str:
        parts = args.split()
        subcommand = parts[0] if parts else "status"
        repo_path = Path(parts[1]).expanduser() if len(parts) > 1 else session.agent.config.workspace
        pr_ref = parts[2] if len(parts) > 2 else None

        try:
            if subcommand == "status":
                return format_pr_status(get_pr_status(repo_path=repo_path, pr_ref=pr_ref))
            if subcommand == "bundle":
                bundle = write_review_bundle_or_local(repo_path=repo_path, pr_ref=pr_ref)
                if hasattr(bundle, "status"):
                    return format_review_bundle_result(bundle)
                return format_local_review_bundle_result(bundle)
        except Exception as exc:
            return f"GitHub PR command failed: {exc}"

        return (
            "Usage:\n"
            "  /github-pr status [repo_path] [pr_ref]\n"
            "  /github-pr bundle [repo_path] [pr_ref]"
        )


class ReviewLoopCommand(Command):
    """Install recurring review loops for repos."""

    name = "/review-loop"
    aliases = []
    description = "Install a recurring review-loop cron for a repository"

    async def execute(self, args: str, session: "AgentSession") -> str:
        parts = args.split()
        subcommand = parts[0] if parts else "install"

        if subcommand != "install":
            return (
                "Usage:\n"
                f"  /review-loop install [repo_path] [schedule]\n"
                f"Default schedule: {DEFAULT_REVIEW_LOOP_SCHEDULE}"
            )

        repo_path = Path(parts[1]).expanduser() if len(parts) > 1 else session.agent.config.workspace
        schedule = " ".join(parts[2:]).strip() or DEFAULT_REVIEW_LOOP_SCHEDULE

        try:
            result = install_review_loop(
                workspace=session.agent.config.workspace,
                repo_path=repo_path,
                schedule=schedule,
            )
        except Exception as exc:
            return f"Review loop install failed: {exc}"

        return format_review_loop_install(result)


class PluginsCommand(Command):
    """List discovered plugins and their capabilities."""

    name = "/plugins"
    aliases = []
    description = "List discovered SemeClaw plugins"

    async def execute(self, args: str, session: "AgentSession") -> str:
        loader = PluginLoader.from_workspace(session.agent.config.workspace)
        plugins = loader.discover_plugins()
        if not plugins:
            return "No plugins discovered."

        lines = [f"Plugins ({len(plugins)}):"]
        for plugin in plugins:
            tool_info = "tools" if plugin.tool_factory else "docs-only"
            skill_count = len(plugin.skill_paths)
            desc = f" - {plugin.description}" if plugin.description else ""
            lines.append(
                f"  - {plugin.id} ({plugin.name}, {tool_info}, skill_paths={skill_count}){desc}"
            )
        return "\n".join(lines)
