"""SemeClaw onboarding — auto-detect providers and guide first-time setup."""

from __future__ import annotations

import os
import socket
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import typer
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

console = Console()


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

@dataclass
class ProviderDef:
    id: str
    label: str
    env_key: str                    # primary env var that signals availability
    default_model: str
    model_choices: list[str]
    needs_api_key: bool = True
    api_base_env: str = ""          # env var for custom base URL (Ollama, OpenRouter, etc.)
    probe_url: str = ""             # HTTP URL to probe (for local providers)
    docs_url: str = ""


PROVIDERS: list[ProviderDef] = [
    ProviderDef(
        id="anthropic",
        label="Anthropic (Claude)",
        env_key="ANTHROPIC_API_KEY",
        default_model="claude-sonnet-4-6",
        model_choices=[
            "claude-opus-4-6",
            "claude-sonnet-4-6",
            "claude-haiku-4-5",
        ],
        docs_url="https://console.anthropic.com/settings/keys",
    ),
    ProviderDef(
        id="openai",
        label="OpenAI (GPT)",
        env_key="OPENAI_API_KEY",
        default_model="gpt-4.1",
        model_choices=["gpt-4.1", "gpt-4o", "gpt-4o-mini"],
        docs_url="https://platform.openai.com/api-keys",
    ),
    ProviderDef(
        id="dashscope",
        label="Alibaba / DashScope (Qwen)",
        env_key="DASHSCOPE_API_KEY",
        default_model="qwen-plus",
        model_choices=["qwen-plus", "qwen-turbo", "qwen-max"],
        docs_url="https://dashscope.console.aliyun.com/apiKey",
    ),
    ProviderDef(
        id="openrouter",
        label="OpenRouter (multi-model, free tier)",
        env_key="OPENROUTER_API_KEY",
        default_model="openrouter/qwen/qwen3.6-plus:free",
        model_choices=[
            "openrouter/qwen/qwen3.6-plus:free",
            "openrouter/anthropic/claude-sonnet-4-6",
            "openrouter/openai/gpt-4.1",
        ],
        docs_url="https://openrouter.ai/keys",
    ),
    ProviderDef(
        id="gemini",
        label="Google Gemini",
        env_key="GEMINI_API_KEY",
        default_model="gemini/gemini-2.5-flash",
        model_choices=["gemini/gemini-2.5-flash", "gemini/gemini-2.5-pro"],
        docs_url="https://aistudio.google.com/app/apikey",
    ),
    ProviderDef(
        id="ollama",
        label="Ollama (local, FREE)",
        env_key="OLLAMA_BASE_URL",
        default_model="ollama/qwen3:8b",
        model_choices=["ollama/qwen3:8b", "ollama/qwen2.5-coder:7b", "ollama/llama3"],
        needs_api_key=False,
        probe_url="http://localhost:11434",
        docs_url="https://ollama.com",
    ),
]

# Env-var → provider id fast lookup (also catches GOOGLE_API_KEY)
_ENV_TO_PROVIDER: dict[str, str] = {p.env_key: p.id for p in PROVIDERS}
_ENV_TO_PROVIDER["GOOGLE_API_KEY"] = "gemini"

PROVIDER_BY_ID: dict[str, ProviderDef] = {p.id: p for p in PROVIDERS}


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

def _probe_tcp(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _probe_http(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310
            return r.status < 500
    except Exception:
        return False


@dataclass
class DetectedProvider:
    provider: ProviderDef
    source: str           # "env" | "local_probe"
    api_key: str = ""


def detect_providers() -> list[DetectedProvider]:
    """Scan environment and localhost for available LLM providers."""
    found: list[DetectedProvider] = []
    seen: set[str] = set()

    for env_var, provider_id in _ENV_TO_PROVIDER.items():
        val = os.environ.get(env_var, "").strip()
        if val and provider_id not in seen:
            found.append(DetectedProvider(
                provider=PROVIDER_BY_ID[provider_id],
                source="env",
                api_key=val,
            ))
            seen.add(provider_id)

    # Ollama local probe (no key needed)
    if "ollama" not in seen:
        ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        if _probe_http(ollama_url + "/api/tags"):
            found.append(DetectedProvider(
                provider=PROVIDER_BY_ID["ollama"],
                source="local_probe",
                api_key="ollama",
            ))

    return found


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_llm(provider_id: str, model: str, api_key: str, api_base: str = "") -> tuple[bool, str]:
    """Send a minimal test completion. Returns (ok, error_message)."""
    try:
        import litellm  # type: ignore

        litellm.set_verbose = False

        kwargs: dict = dict(
            model=model,
            messages=[{"role": "user", "content": "reply with the single word: ok"}],
            max_tokens=5,
        )

        if provider_id == "ollama":
            kwargs["api_base"] = api_base or "http://localhost:11434"
            kwargs["api_key"] = "ollama"
        elif provider_id == "openrouter":
            kwargs["api_key"] = api_key
            kwargs["api_base"] = "https://openrouter.ai/api/v1"
        else:
            kwargs["api_key"] = api_key
            if api_base:
                kwargs["api_base"] = api_base

        import asyncio

        async def _call() -> str:
            resp = await litellm.acompletion(**kwargs)
            return resp.choices[0].message.content or ""

        result = asyncio.run(_call())
        return True, result.strip()
    except Exception as exc:
        return False, str(exc)


# ---------------------------------------------------------------------------
# Workspace scaffold
# ---------------------------------------------------------------------------

_EXAMPLE_AGENT_MD = """\
---
name: {name}
description: Your SemeClaw AI agent.
allow_skills: false
llm:
  temperature: 0.7
  max_tokens: 4096
---

You are {name}, an AI assistant powered by SemeClaw.
Be concise, helpful, and direct.
"""

_EXAMPLE_SOUL_MD = """\
## Personality

- Clear and direct
- Curious and analytical
- Collaborative with the user
"""


def scaffold_workspace(workspace: Path, default_agent: str = "assistant") -> None:
    """Create the minimum directory structure for a fresh workspace."""
    (workspace / "agents" / default_agent).mkdir(parents=True, exist_ok=True)
    (workspace / "skills").mkdir(parents=True, exist_ok=True)
    (workspace / "crons").mkdir(parents=True, exist_ok=True)
    (workspace / "memories").mkdir(parents=True, exist_ok=True)

    agent_dir = workspace / "agents" / default_agent
    agent_md = agent_dir / "AGENT.md"
    soul_md = agent_dir / "SOUL.md"

    if not agent_md.exists():
        agent_md.write_text(_EXAMPLE_AGENT_MD.format(name=default_agent.capitalize()))
    if not soul_md.exists():
        soul_md.write_text(_EXAMPLE_SOUL_MD)


# ---------------------------------------------------------------------------
# Config writer
# ---------------------------------------------------------------------------

def write_config(
    workspace: Path,
    provider: ProviderDef,
    model: str,
    api_key: str,
    default_agent: str,
    telegram_token: str = "",
    telegram_chat_id: str = "",
    brave_key: str = "",
) -> Path:
    cfg: dict = {
        "llm": {
            "provider": provider.id,
            "model": model,
            "api_key": api_key if not api_key.startswith("$") else api_key,
            "temperature": 0.7,
            "max_tokens": 4096,
        },
        "default_agent": default_agent,
    }

    if telegram_token:
        cfg["channels"] = {
            "enabled": True,
            "telegram": {
                "bot_token": telegram_token,
                "allowed_user_ids": [telegram_chat_id] if telegram_chat_id else [],
            },
        }

    if brave_key:
        cfg["websearch"] = {"provider": "brave", "api_key": brave_key}

    out = workspace / "config.user.yaml"
    with open(out, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)
    return out


# ---------------------------------------------------------------------------
# Main onboarding flow
# ---------------------------------------------------------------------------

def run_onboard(workspace: Path, force: bool = False) -> bool:
    """
    Full interactive onboarding wizard.
    Returns True if config was written successfully, False if aborted.
    """
    config_file = workspace / "config.user.yaml"

    console.print()
    console.print(Panel(
        "[bold cyan]SemeClaw Setup Wizard[/bold cyan]\n"
        "[dim]Let's get you connected in under 2 minutes.[/dim]",
        border_style="cyan",
        padding=(1, 4),
    ))

    if config_file.exists() and not force:
        console.print(f"\n[yellow]Config already exists:[/yellow] {config_file}")
        overwrite = Confirm.ask("Overwrite and reconfigure?", default=False)
        if not overwrite:
            console.print("[dim]Keeping existing config. Run [bold]semeclaw chat[/bold] to start.[/dim]")
            return False

    # ------------------------------------------------------------------
    # Step 1 — auto-detect
    # ------------------------------------------------------------------
    console.print("\n[bold]Step 1 — Scanning for available providers...[/bold]")
    detected = detect_providers()

    chosen_provider: Optional[ProviderDef] = None
    chosen_key: str = ""

    if detected:
        table = Table(show_header=True, header_style="bold green", box=None, padding=(0, 2))
        table.add_column("#", style="dim", width=3)
        table.add_column("Provider")
        table.add_column("Model (default)")
        table.add_column("Source", style="dim")

        for i, d in enumerate(detected, 1):
            source_label = "[green]env var[/green]" if d.source == "env" else "[blue]local[/blue]"
            table.add_row(str(i), d.provider.label, d.provider.default_model, source_label)

        console.print(table)

        if len(detected) == 1:
            use_detected = Confirm.ask(
                f"\nUse [bold]{detected[0].provider.label}[/bold]?",
                default=True,
            )
            if use_detected:
                chosen_provider = detected[0].provider
                chosen_key = detected[0].api_key
        else:
            console.print("\n[dim]Multiple providers detected.[/dim]")
            pick = Prompt.ask(
                "Choose a provider number (or press Enter to pick manually)",
                default="",
            ).strip()
            if pick.isdigit() and 1 <= int(pick) <= len(detected):
                d = detected[int(pick) - 1]
                chosen_provider = d.provider
                chosen_key = d.api_key
    else:
        console.print("[dim]No providers detected in environment.[/dim]")

    # ------------------------------------------------------------------
    # Step 2 — manual provider selection (if auto-detect didn't resolve)
    # ------------------------------------------------------------------
    if chosen_provider is None:
        console.print("\n[bold]Step 2 — Choose a provider[/bold]")

        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        table.add_column("#", style="dim", width=3)
        table.add_column("Provider")
        table.add_column("Default model")
        table.add_column("Cost", style="dim")

        costs = {
            "anthropic": "paid",
            "openai": "paid",
            "dashscope": "paid",
            "openrouter": "free tier available",
            "gemini": "free tier available",
            "ollama": "FREE (local)",
        }
        for i, p in enumerate(PROVIDERS, 1):
            table.add_row(str(i), p.label, p.default_model, costs.get(p.id, ""))

        console.print(table)

        while True:
            pick = Prompt.ask("\nProvider number").strip()
            if pick.isdigit() and 1 <= int(pick) <= len(PROVIDERS):
                chosen_provider = PROVIDERS[int(pick) - 1]
                break
            console.print("[red]Invalid choice, try again.[/red]")

    # ------------------------------------------------------------------
    # Step 3 — API key
    # ------------------------------------------------------------------
    if chosen_provider.needs_api_key and not chosen_key:
        console.print(f"\n[bold]Step 3 — API key for {chosen_provider.label}[/bold]")
        if chosen_provider.docs_url:
            console.print(f"[dim]Get one at: {chosen_provider.docs_url}[/dim]")
        while True:
            chosen_key = Prompt.ask("API key", password=True).strip()
            if chosen_key:
                break
            console.print("[red]API key cannot be empty.[/red]")
    elif not chosen_provider.needs_api_key:
        chosen_key = "ollama"

    # ------------------------------------------------------------------
    # Step 4 — Model selection
    # ------------------------------------------------------------------
    console.print(f"\n[bold]Step 4 — Model[/bold] (default: {chosen_provider.default_model})")
    model_list = "  ".join(f"[dim]{m}[/dim]" for m in chosen_provider.model_choices)
    console.print(f"Options: {model_list}")
    chosen_model = Prompt.ask("Model", default=chosen_provider.default_model).strip()

    # ------------------------------------------------------------------
    # Step 5 — Validate connection
    # ------------------------------------------------------------------
    console.print(f"\n[bold]Step 5 — Testing connection to {chosen_provider.label}...[/bold]")
    with console.status("[cyan]Sending test message...[/cyan]"):
        ok, detail = validate_llm(chosen_provider.id, chosen_model, chosen_key)

    if ok:
        console.print(f"[green]✓ Connection successful[/green] [dim](got: {detail!r})[/dim]")
    else:
        console.print(f"[red]✗ Connection failed:[/red] {detail}")
        retry = Confirm.ask("Continue anyway and save config?", default=False)
        if not retry:
            console.print("[dim]Aborted. Run [bold]semeclaw init[/bold] to try again.[/dim]")
            return False

    # ------------------------------------------------------------------
    # Step 6 — Default agent name
    # ------------------------------------------------------------------
    console.print("\n[bold]Step 6 — Default agent[/bold]")
    agents_dir = workspace / "agents"
    existing: list[str] = []
    if agents_dir.exists():
        existing = [d.name for d in agents_dir.iterdir() if d.is_dir()]

    if existing:
        console.print(f"[dim]Existing agents: {', '.join(existing)}[/dim]")
        default_agent = Prompt.ask("Default agent", default=existing[0]).strip() or existing[0]
    else:
        default_agent = Prompt.ask("Default agent name", default="assistant").strip() or "assistant"
        console.print(f"[dim]Will scaffold a new agent: {default_agent}[/dim]")

    # ------------------------------------------------------------------
    # Step 7 — Optional Telegram
    # ------------------------------------------------------------------
    console.print("\n[bold]Step 7 — Telegram (optional)[/bold]")
    telegram_token = ""
    telegram_chat_id = ""
    if Confirm.ask("Enable Telegram channel?", default=False):
        telegram_token = Prompt.ask("Bot token (from @BotFather)", password=True).strip()
        telegram_chat_id = Prompt.ask("Your Telegram chat ID (or leave blank)", default="").strip()

    # ------------------------------------------------------------------
    # Step 8 — Optional web search
    # ------------------------------------------------------------------
    console.print("\n[bold]Step 8 — Web search (optional)[/bold]")
    brave_key = ""
    if Confirm.ask("Enable Brave web search?", default=False):
        console.print("[dim]Get a key at https://brave.com/search/api/[/dim]")
        brave_key = Prompt.ask("Brave Search API key", password=True).strip()

    # ------------------------------------------------------------------
    # Write config + scaffold
    # ------------------------------------------------------------------
    workspace.mkdir(parents=True, exist_ok=True)
    scaffold_workspace(workspace, default_agent)

    config_path = write_config(
        workspace=workspace,
        provider=chosen_provider,
        model=chosen_model,
        api_key=chosen_key,
        default_agent=default_agent,
        telegram_token=telegram_token,
        telegram_chat_id=telegram_chat_id,
        brave_key=brave_key,
    )

    # ------------------------------------------------------------------
    # Success summary
    # ------------------------------------------------------------------
    console.print()
    console.print(Panel(
        Text.assemble(
            ("✓ SemeClaw is ready!\n\n", "bold green"),
            ("Config: ", "dim"), (str(config_path), "cyan"), ("\n", ""),
            ("Provider: ", "dim"), (chosen_provider.label, "white"), ("\n", ""),
            ("Model: ", "dim"), (chosen_model, "white"), ("\n", ""),
            ("Agent: ", "dim"), (default_agent, "white"), ("\n\n", ""),
            ("Next steps:\n", "bold"),
            ("  semeclaw chat       ", "cyan"), ("→ start chatting\n", "dim"),
            ("  semeclaw server     ", "cyan"), ("→ start the API server\n", "dim"),
            ("  semeclaw init       ", "cyan"), ("→ re-run this wizard\n", "dim"),
        ),
        border_style="green",
        padding=(1, 3),
    ))

    return True
