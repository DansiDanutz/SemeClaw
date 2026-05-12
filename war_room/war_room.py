"""
War Room — Orchestrator
Dan's Lab Agent Fleet coordinator.

Pipeline:
  User task → [Memory context] → Research → Architect|Strategist → [Coder] → Writer → Paperclip issue

Usage:
  python war_room/war_room.py run "Research open-source AI agent marketplaces"
  python war_room/war_room.py run "Build X feature" --agents=research,architect,coder,writer
  python war_room/war_room.py status
  python war_room/war_room.py board
  python war_room/war_room.py memory
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx
import litellm
from rich.console import Console

# ---------------------------------------------------------------------------
# Bootstrap path so this runs from repo root
# ---------------------------------------------------------------------------
ROOT = Path(__file__).parent.parent
WAR_ROOM_DIR = Path(__file__).parent
DEFAULT_AGENTS = ("research", "strategist", "writer")
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(WAR_ROOM_DIR))  # so local modules are importable directly

from adapters.multica import MulticaAdapter
from adapters.paperclip import PaperclipAdapter
from memory import WarRoomMemory
from paperclip_bridge import AGENT_ASSIGNEES
from research_tools import ResearchTools

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("war_room")
console = Console()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
AGENTS_DIR = WAR_ROOM_DIR / "agents"
RESEARCH_DIR = WAR_ROOM_DIR / "research"
LOGS_DIR = WAR_ROOM_DIR / "logs"
STATE_FILE = WAR_ROOM_DIR / "shared_state.json"
TASK_QUEUE_FILE = WAR_ROOM_DIR / "task_queue.json"
TELEGRAM_CHAT_FILE = ROOT / ".telegram_chat_id"

RESEARCH_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# LLM config — per-agent model routing
# ---------------------------------------------------------------------------
DEFAULT_MODEL = "dashscope/qwen3.6-plus"
DASHSCOPE_INTL_BASE = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

# ── Per-agent model assignments ───────────────────────────────────────────────
# Each agent gets the model best suited to its role.
# Credentials are read from ~/.openclaw/openclaw.json providers section.
AGENT_MODELS: dict[str, dict] = {
    # Research: Qwen 3.6 Plus FREE — 1M context, multimodal, best for synthesis
    "research": {
        "model": "openrouter/qwen/qwen3.6-plus:free",
        "api_base": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "api_key_fallback": "sk-or-v1-fa790746dcf6b850af34c",  # from openclaw.json
        "max_tokens": 8192,
        "temperature": 0.2,
    },
    # Strategist / GSD: Kimi K2.5 — strongest structured reasoning + planning
    "strategist": {
        "model": "kimi-k2.5",
        "api_base": "https://api.moonshot.ai/v1",
        "api_key_env": "MOONSHOT_API_KEY",
        "api_key_fallback": "sk-kimi-TWpgpQOsPq1Qfbhre1wblye1FoRnhhGLfdSgVJaHdPDq0b6T6j4qDYnzfNOflmnx",
        "max_tokens": 8192,
        "temperature": 0.3,
    },
    # Architect: Z.ai GLM-5 — code-aware architect, 200K context, 1yr subscription
    "architect": {
        "model": "glm-5",
        "api_base": "https://open.bigmodel.cn/api/paas/v4",
        "api_key_env": "ZHIPU_API_KEY",
        "api_key_fallback": "fbf824b80eda40b09cc658f7ddbf72",  # from openclaw.json
        "max_tokens": 8192,
        "temperature": 0.25,
    },
    # Coder: Z.ai GLM-5 — same subscription, strong at implementation
    "coder": {
        "model": "glm-5",
        "api_base": "https://open.bigmodel.cn/api/paas/v4",
        "api_key_env": "ZHIPU_API_KEY",
        "api_key_fallback": "fbf824b80eda40b09cc658f7ddbf72",
        "max_tokens": 8192,
        "temperature": 0.2,
    },
    # Writer / Hermes: GPT-5.4 via ChatGPT Pro subscription (local proxy :8995)
    "writer": {
        "model": "gpt-5.4",
        "api_base": "http://127.0.0.1:8995/v1",
        "api_key_env": "OPENAI_API_KEY",
        "api_key_fallback": "sk-local",
        "max_tokens": 4096,
        "temperature": 0.4,
    },
    # Narrator / Host: Gemma 4 local via Ollama — zero-cost, instant narration
    "narrator": {
        "model": "ollama/gemma4",
        "api_base": "http://127.0.0.1:11434",
        "api_key_env": None,
        "api_key_fallback": "ollama",
        "max_tokens": 2048,
        "temperature": 0.5,
    },
    # David (chairman): Gemma 4 local — hosts meetings, fast
    "david": {
        "model": "ollama/gemma4",
        "api_base": "http://127.0.0.1:11434",
        "api_key_env": None,
        "api_key_fallback": "ollama",
        "max_tokens": 2048,
        "temperature": 0.4,
    },
}

# Fallback if an agent_id isn't in AGENT_MODELS
_FALLBACK_MODEL_CFG = {
    "model": "openrouter/qwen/qwen3.6-plus:free",
    "api_base": "https://openrouter.ai/api/v1",
    "api_key_env": "OPENROUTER_API_KEY",
    "api_key_fallback": "sk-or-v1-fa790746dcf6b850af34c",
    "max_tokens": 4096,
    "temperature": 0.3,
}


def _get_model_cfg(agent_id: str) -> dict:
    """Return the model config for an agent, resolving API keys from env."""
    cfg = dict(AGENT_MODELS.get(agent_id, _FALLBACK_MODEL_CFG))
    # Resolve API key: env var first, then fallback
    key_env = cfg.get("api_key_env")
    if key_env:
        cfg["api_key"] = os.environ.get(key_env) or cfg.get("api_key_fallback", "")
    else:
        cfg["api_key"] = cfg.get("api_key_fallback", "ollama")
    return cfg


def _load_api_keys_from_openclaw():
    """Load provider API keys from openclaw.json into os.environ."""
    try:
        import json as _json

        cfg = _json.loads(_OPENCLAW_JSON.read_text())
        providers = cfg.get("models", {}).get("providers", {})
        key_map = {
            "openrouter": "OPENROUTER_API_KEY",
            "moonshot": "MOONSHOT_API_KEY",
            "zai": "ZHIPU_API_KEY",
            "openai": "OPENAI_API_KEY",
            "google": "GOOGLE_API_KEY",
        }
        for pname, env_name in key_map.items():
            if not os.environ.get(env_name):
                key = providers.get(pname, {}).get("apiKey", "")
                if key and not key.startswith("${"):
                    os.environ[env_name] = key
                    logger.debug("Loaded %s from openclaw.json[%s]", env_name, pname)
    except Exception as e:
        logger.debug("openclaw key load: %s", e)


_OPENCLAW_JSON = Path.home() / ".openclaw" / "openclaw.json"
_OPENCLAW_RUNTIME_GLOB = str(Path.home() / ".openclaw" / "openclaw.runtime.json.*")


def _bootstrap_api_keys() -> None:
    """
    Ensure DASHSCOPE_API_KEY (and other keys) are set in os.environ
    before any litellm call.

    Resolution order:
    1. Already in os.environ → nothing to do
    2. ~/.openclaw/openclaw.json  → primary fleet config
    3. ~/.openclaw/fleet.env      → secondary fleet env
    4. Latest openclaw.runtime.json.* temp file → fallback
    """
    KEY = "DASHSCOPE_API_KEY"
    if os.environ.get(KEY):
        return  # already set

    # 1. Main openclaw config (JSON)
    if _OPENCLAW_JSON.exists():
        try:
            import json as _json

            cfg = _json.loads(_OPENCLAW_JSON.read_text())
            val = cfg.get(KEY) or cfg.get("env", {}).get(KEY)
            if val and not val.startswith("${"):
                os.environ[KEY] = val
                logger.debug("DASHSCOPE_API_KEY loaded from openclaw.json")
                return
        except Exception:
            pass

    # 2. fleet.env
    fleet_env = Path.home() / ".openclaw" / "fleet.env"
    if fleet_env.exists():
        try:
            for line in fleet_env.read_text().splitlines():
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                if "=" not in line or line.startswith("#"):
                    continue
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if k == KEY and v and not v.startswith("${"):
                    os.environ[KEY] = v
                    logger.debug("DASHSCOPE_API_KEY loaded from fleet.env")
                    return
        except Exception:
            pass

    # 3. Latest runtime JSON temp file (openclaw writes these during startup)
    import glob as _glob

    runtime_files = sorted(_glob.glob(_OPENCLAW_RUNTIME_GLOB))
    if runtime_files:
        try:
            import json as _json

            cfg = _json.loads(Path(runtime_files[-1]).read_text())
            val = cfg.get(KEY) or cfg.get("env", {}).get(KEY)
            if val and not val.startswith("${"):
                os.environ[KEY] = val
                logger.debug("DASHSCOPE_API_KEY loaded from runtime config")
                return
        except Exception:
            pass

    logger.warning("DASHSCOPE_API_KEY not found in any source. Set it via: export DASHSCOPE_API_KEY=sk-...")


# Bootstrap keys immediately at import time
_bootstrap_api_keys()
_load_api_keys_from_openclaw()  # loads Moonshot, ZhiPu, OpenRouter, OpenAI, Google keys


def _load_model() -> str:
    cfg_path = ROOT / "default_workspace" / "config.yaml"
    if not cfg_path.exists():
        return DEFAULT_MODEL
    try:
        import yaml

        cfg = yaml.safe_load(cfg_path.read_text())
        return cfg.get("model", DEFAULT_MODEL)
    except Exception:
        return DEFAULT_MODEL


def resolve_model(root: Path, model: str | None = None) -> str:
    """Resolve the LLM model to use."""
    if model:
        return model
    return _load_model()


def _load_telegram_creds() -> tuple[str | None, str | None]:
    """
    Load Telegram bot token + Dan's chat ID from multiple fallback sources:
    1. SemeClaw config.yaml
    2. /etc/openclaw-env (fleet env)
    3. ~/.openclaw/fleet.env
    4. env vars TELEGRAM_BOT_TOKEN / DLS_DAN_CHAT_ID
    Returns (bot_token, chat_id) — either may be None.
    """
    bot_token = None
    chat_id = None

    # 1. SemeClaw config.yaml
    cfg_path = ROOT / "default_workspace" / "config.yaml"
    if cfg_path.exists():
        try:
            import yaml

            cfg = yaml.safe_load(cfg_path.read_text())
            tg = cfg.get("telegram", {})
            bot_token = tg.get("bot_token") or tg.get("token")
            chat_id = tg.get("chat_id") or tg.get("dan_chat_id")
        except Exception:
            pass

    # 2. ~/.telegram_chat_id file (legacy)
    chat_id_file = ROOT / ".telegram_chat_id"
    if not chat_id and chat_id_file.exists():
        chat_id = chat_id_file.read_text().strip()

    # 3. Fleet env files
    fleet_env_paths = [
        Path("/etc/openclaw-env"),
        Path.home() / ".openclaw" / "fleet.env",
    ]
    for env_path in fleet_env_paths:
        if env_path.exists() and (not bot_token or not chat_id):
            try:
                for line in env_path.read_text().splitlines():
                    line = line.strip()
                    if line.startswith("export "):
                        line = line[7:]
                    if "=" not in line or line.startswith("#"):
                        continue
                    k, v = line.split("=", 1)
                    v = v.strip().strip('"').strip("'")
                    if not v or "${" in v:
                        continue
                    if k == "DLS_TELEGRAM_BOT_TOKEN" and not bot_token:
                        bot_token = v
                    elif k == "DLS_DAN_CHAT_ID" and not chat_id:
                        chat_id = v
            except Exception:
                pass

    # 4. Environment variables (last resort)
    if not bot_token:
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("DLS_TELEGRAM_BOT_TOKEN")
    if not chat_id:
        chat_id = os.environ.get("DLS_DAN_CHAT_ID")

    return bot_token, chat_id


# ---------------------------------------------------------------------------
# Agent definitions (load from markdown files)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentDef:
    id: str
    system_prompt: str

    def __getitem__(self, key: str):
        return getattr(self, key)

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)

    def get(self, key: str, default=None):
        return getattr(self, key, default)


def load_agent_def(agent_id: str) -> AgentDef:
    """Load agent markdown file and return an AgentDef (front-matter stripped)."""
    path = AGENTS_DIR / f"{agent_id}.md"
    if not path.exists():
        raise FileNotFoundError(f"Agent def not found: {path}")
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        body = parts[2].strip() if len(parts) >= 3 else text
    else:
        body = text
    return AgentDef(id=agent_id, system_prompt=body)


# ---------------------------------------------------------------------------
# Single agent call
# ---------------------------------------------------------------------------

# Agents that get access to tools (run_shell, run_code, search, etc.)
TOOL_ENABLED_AGENTS = {"research", "coder"}


async def call_agent(
    agent_id: str,
    task: str,
    context: str = "",
    model: str | None = None,
    tools: ResearchTools | None = None,
) -> str:
    """
    Call a War Room agent with a task.
    Uses per-agent model routing (AGENT_MODELS). Falls back to global model.
    Returns the agent's response as a string.
    """
    # Load per-agent model config (ignores the old single-model parameter)
    mcfg = _get_model_cfg(agent_id)
    use_model = model or mcfg["model"]
    api_base = mcfg.get("api_base")
    api_key = mcfg.get("api_key") or "sk-placeholder"
    max_tok = mcfg.get("max_tokens", 4096)
    temp = mcfg.get("temperature", 0.3)

    agent = load_agent_def(agent_id)
    system = agent["system_prompt"]
    user_msg = task
    if context:
        user_msg = f"## Context from previous agents\n\n{context}\n\n---\n\n## Your Task\n\n{task}"

    logger.info("🤖 [%s] → %s (base: %s)", agent_id, use_model, (api_base or "")[:40])

    if agent_id in TOOL_ENABLED_AGENTS and tools:
        system += tools.get_available_tools_description()

    messages = [{"role": "user", "content": user_msg}]

    async def _llm_call(msgs):
        kwargs = dict(
            model=use_model,
            messages=msgs,
            max_tokens=max_tok,
            temperature=temp,
        )
        if api_base:
            kwargs["api_base"] = api_base
        # litellm uses OPENAI_API_KEY / api_key param
        if api_key and api_key not in ("ollama", "sk-placeholder"):
            kwargs["api_key"] = api_key
        # system prompt support varies by provider
        try:
            return await litellm.acompletion(system=system, **kwargs)
        except TypeError:
            # Some providers don't take system= as kwarg
            msgs_with_sys = [{"role": "system", "content": system}] + msgs
            return await litellm.acompletion(**{**kwargs, "messages": msgs_with_sys})

    try:
        response = await _llm_call(messages)
    except Exception as e:
        logger.warning("⚠️  [%s] %s failed (%s) — falling back to Qwen 3.6 FREE", agent_id, use_model, e)
        # Fallback: Qwen 3.6 Plus FREE via OpenRouter
        use_model = "openrouter/qwen/qwen3.6-plus:free"
        api_base = "https://openrouter.ai/api/v1"
        api_key = os.environ.get("OPENROUTER_API_KEY", "sk-or-v1-fa790746dcf6b850af34c")
        response = await litellm.acompletion(
            model=use_model,
            messages=messages,
            system=system,
            max_tokens=4096,
            temperature=0.3,
            api_base=api_base,
            api_key=api_key,
        )

    result = response.choices[0].message.content or ""

    # Tool-use loop for tool-enabled agents
    if agent_id in TOOL_ENABLED_AGENTS and tools:
        tool_iterations = 0
        max_tool_iterations = 8 if agent_id == "coder" else 5
        while tool_iterations < max_tool_iterations:
            tool_call = _parse_tool_call(result, tools)
            if not tool_call:
                break

            tool_name, tool_args = tool_call
            logger.info("🔧 [%s] tool: %s(%s)", agent_id, tool_name, tool_args[:80])

            if tool_name == "search":
                tool_result = await tools.search(tool_args.strip('"').strip("'"))
            elif tool_name == "extract":
                import re

                urls = re.findall(r'https?://[^\s"\']+', tool_args)
                tool_result = await tools.extract(urls) if urls else "[No URLs found]"
            elif tool_name == "browser_navigate":
                tool_result = await tools.browser_navigate(tool_args.strip('"').strip("'").strip())
            elif tool_name == "run_code":
                tool_result = await tools.run_code(tool_args)
            elif tool_name == "run_shell":
                tool_result = await tools.run_shell(tool_args.strip('"').strip("'"))
            else:
                tool_result = f"[Unknown tool: {tool_name}]"

            messages.append({"role": "assistant", "content": result})
            messages.append(
                {
                    "role": "user",
                    "content": f"Tool result from {tool_name}:\n\n{tool_result[:4000]}\n\nContinue.",
                }
            )
            response = await _llm_call(messages)
            result = response.choices[0].message.content or ""
            tool_iterations += 1

    logger.info("✅ [%s] done (%d chars)", agent_id, len(result))
    return result


def _parse_tool_call(response: str, tools: ResearchTools) -> tuple[str, str] | None:
    """
    Parse tool use from agent response.
    Supports call-style: search("query"), run_shell("cmd")
    Supports XML-style: <tool>search</tool><args>query</args>
    """
    import re

    tool_patterns = [
        (r'search\(["\'](.+?)["\']\)', "search"),
        (r"extract\((.+?)\)", "extract"),
        (r'browser_navigate\(["\'](.+?)["\']\)', "browser_navigate"),
        (r"run_code\((.+?)\)", "run_code"),
        (r'run_shell\(["\'](.+?)["\']\)', "run_shell"),
    ]

    for pattern, tool_name in tool_patterns:
        match = re.search(pattern, response, re.DOTALL)
        if match:
            return tool_name, match.group(1)

    # XML-style: <tool>search</tool><args>query</args>
    xml_tool = re.search(r"<tool>(\w+)</tool>\s*<args>(.+?)</args>", response, re.DOTALL)
    if xml_tool:
        return xml_tool.group(1), xml_tool.group(2)

    return None


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


@dataclass
class PipelineResult:
    run_id: str
    task: str
    agents_run: list[str]
    output_file: Path
    paperclip_issue: dict | None
    multica_issue: dict | None
    results: dict[str, str] = field(default_factory=dict)

    def to_public(self) -> dict:
        return {
            "run_id": self.run_id,
            "task": self.task,
            "agents_run": self.agents_run,
            "output_file": str(self.output_file),
            "paperclip_issue": self.paperclip_issue,
            "multica_issue": self.multica_issue,
            "results": {k: v[:200] + "…" for k, v in self.results.items()},
        }


class WarRoomPipeline:
    """
    Orchestrates the full pipeline:
      [Memory] → Research → Architect|Strategist → [Coder] → Writer → Paperclip issue

    Default pipeline: research → strategist → writer
    With coder:       research → architect → coder → writer
    """

    def __init__(self, model: str | None = None):
        self.model = resolve_model(ROOT, model)
        self.results: dict[str, str] = {}
        self.memory = WarRoomMemory(WAR_ROOM_DIR / "memory")

    async def run(
        self,
        task: str,
        agents: list[str] | None = None,
        project: str = "NERVIX",
        notify_telegram: bool = True,
    ) -> dict:
        """
        Run the pipeline.
        agents: which agents to involve (default: research → strategist → writer)
        Returns summary dict with results and Paperclip issue.
        """
        agents = agents or ["research", "strategist", "writer"]
        run_id = str(uuid.uuid4())[:8]
        started = datetime.now(timezone.utc).isoformat()

        logger.info("🚀 War Room pipeline | run_id=%s | agents=%s", run_id, " → ".join(agents))
        logger.info("   Task: %s", task[:80])

        # Load relevant memory from prior runs
        prior_memory = self.memory.load_relevant(task)
        if prior_memory:
            logger.info("🧠 Loaded %d prior memory entries for context", len(prior_memory))

        # Build initial context with memory
        context = ""
        if prior_memory:
            mem_lines = ["## Prior Research from Memory\n"]
            for entry in prior_memory:
                mem_lines.append(
                    f"### [{entry['date']}] {entry['topic']}\n{entry['summary']}\n"
                    f"_(Full report: {entry.get('file', 'n/a')})_\n"
                )
            context = "\n".join(mem_lines)

        self.results = {}
        research_tools = ResearchTools()

        for agent_id in agents:
            logger.info("▶️  Running agent: %s", agent_id)
            output = await call_agent(
                agent_id,
                task,
                context=context,
                model=self.model,
                tools=research_tools,
            )
            self.results[agent_id] = output
            context += f"\n\n## Output from {agent_id.capitalize()} Agent\n\n{output}"

        # Save report
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        slug = task[:40].lower().replace(" ", "-").replace("/", "-")
        out_file = RESEARCH_DIR / f"{slug}-{date_str}.md"
        out_file.write_text(self._build_report(task, agents, started), encoding="utf-8")
        logger.info("📄 Saved report: %s", out_file.name)

        # Save to memory for future runs
        self.memory.save(
            topic=task[:80],
            summary=self._build_memory_summary(task, agents),
            run_id=run_id,
            file=str(out_file),
        )
        logger.info("🧠 Saved to memory")

        # Create Paperclip issue from writer output
        paperclip_issue = None
        multica_issue = None
        succeeded = True

        try:
            writer_output = self.results.get("writer", "")
            title, description, ac = self._parse_writer_output(writer_output, task)
            assignee = AGENT_ASSIGNEES.get(agents[-1], "Hermes")

            pc = PaperclipAdapter()
            try:
                paperclip_issue = await pc.create_issue(
                    title=title,
                    description=description,
                    project=project,
                    assignee=assignee,
                    labels=["war-room", "auto-generated"],
                    priority="medium",
                    acceptance_criteria=ac,
                )
                logger.info("📌 Paperclip issue: %s", paperclip_issue.get("id"))
            finally:
                await pc.close()
        except Exception as e:
            logger.warning("Paperclip issue creation failed: %s", e)
            succeeded = False

        if notify_telegram:
            await self._telegram_notify(task, out_file, paperclip_issue, agents)

        self._update_state(run_id, task, agents, paperclip_issue)
        self._log_run(run_id, task, agents, started, paperclip_issue)

        return PipelineResult(
            run_id=run_id,
            task=task,
            agents_run=agents,
            output_file=out_file,
            paperclip_issue=paperclip_issue,
            multica_issue=multica_issue,
            results=self.results,
        )

    def _build_report(self, task: str, agents: list[str], started: str) -> str:
        lines = [
            "# War Room Report",
            f"**Task:** {task}",
            f"**Date:** {started[:10]}",
            f"**Agents:** {' → '.join(a.capitalize() for a in agents)}",
            "",
        ]
        for agent_id in agents:
            output = self.results.get(agent_id, "")
            lines += [f"\n---\n\n## {agent_id.capitalize()} Agent Output\n\n{output}"]
        return "\n".join(lines)

    def _build_memory_summary(self, task: str, agents: list[str]) -> str:
        """Build a compact summary for memory storage."""
        parts = [f"Task: {task}"]
        # Prefer research output for memory, fall back to any agent
        for agent_id in ["research", "strategist", "architect", "writer", "coder"]:
            if agent_id in self.results:
                # Take first 500 chars of each agent output
                parts.append(f"\n[{agent_id.capitalize()}]:\n{self.results[agent_id][:500]}")
        return "\n".join(parts)

    def _parse_writer_output(self, writer_output: str, fallback_task: str) -> tuple[str, str, list[str]]:
        title = f"[War Room] {fallback_task[:70]}"
        description = writer_output[:1000] if writer_output else fallback_task
        ac: list[str] = []

        for line in writer_output.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("title:"):
                title = stripped[6:].strip()
            elif stripped.startswith("# "):
                title = stripped[2:].strip()
            if "- [ ]" in stripped:
                ac.append(stripped.replace("- [ ]", "").strip())

        return title, description, ac

    async def _telegram_notify(
        self,
        task: str,
        report_file: Path,
        issue: dict | None,
        agents: list[str],
    ):
        """Send Telegram notification — reads credentials from multiple sources."""
        bot_token, chat_id = _load_telegram_creds()

        if not bot_token or not chat_id:
            logger.debug("Telegram notify skipped — no credentials found")
            return

        issue_id = issue.get("id", "?") if issue else "?"
        pipeline_str = " → ".join(a.capitalize() for a in agents)

        msg = (
            f"🏛 <b>War Room complete</b>\n"
            f"<b>Task:</b> {task[:80]}\n"
            f"<b>Pipeline:</b> {pipeline_str}\n"
            f"<b>Paperclip:</b> {issue_id}\n"
            f"<b>Report:</b> {report_file.name}"
        )
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
                    timeout=10,
                )
                if r.status_code == 200:
                    logger.info("📱 Telegram notification sent to chat %s", chat_id)
                else:
                    logger.warning("Telegram notify HTTP %d: %s", r.status_code, r.text[:200])
        except Exception as e:
            logger.warning("Telegram notification failed: %s", e)

    def _update_state(self, run_id: str, task: str, agents: list[str], issue: dict | None):
        """Update the persistent state file with run metadata."""
        try:
            state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
        except Exception:
            state = {}
        state.setdefault("completed_tasks", [])
        state.setdefault("metrics", {"tasks_run": 0, "tasks_succeeded": 0, "paperclip_issues_created": 0})
        state["completed_tasks"].append(
            {
                "run_id": run_id,
                "task": task,
                "agents": agents,
                "issue_id": issue.get("id") if issue else None,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        state["metrics"]["tasks_run"] += 1
        state["metrics"]["tasks_succeeded"] += 1
        if issue:
            state["metrics"]["paperclip_issues_created"] += 1
        state["last_updated"] = datetime.now(timezone.utc).isoformat()
        STATE_FILE.write_text(json.dumps(state, indent=2))

    def _log_run(self, run_id: str, task: str, agents: list[str], started: str, issue: dict | None):
        log_file = LOGS_DIR / f"run-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"
        record = {
            "run_id": run_id,
            "task": task,
            "agents": agents,
            "started": started,
            "completed": datetime.now(timezone.utc).isoformat(),
            "issue_id": issue.get("id") if issue else None,
        }
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")


# ---------------------------------------------------------------------------
# Helpers (module-level so tests can reach them directly)
# ---------------------------------------------------------------------------


def _slugify(value: str, max_len: int = 40) -> str:
    cleaned = "".join(c if c.isalnum() else "-" for c in value.lower())
    # Collapse consecutive dashes
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")[:max_len].strip("-") or "task"


def _write_report_file(
    task: str,
    agents: list[str],
    started_iso: str,
    results: dict[str, str],
) -> Path:
    """Write a markdown report with YAML front-matter + agent outputs."""
    date_str = started_iso[:10]
    slug = _slugify(task)
    out_file = RESEARCH_DIR / f"{slug}-{date_str}.md"

    front_matter = [
        "---",
        f"task: {task!r}",
        f"date: {date_str}",
        f"started: {started_iso}",
        f"agents: [{', '.join(agents)}]",
        "---",
        "",
    ]
    body: list[str] = [
        "# War Room Report",
        "",
        f"**Task:** {task}",
        f"**Date:** {date_str}",
        f"**Agents:** {' → '.join(a.capitalize() for a in agents)}",
    ]
    for agent_id in agents:
        output = results.get(agent_id, "").strip()
        if not output:
            continue
        body += ["", "---", "", f"## {agent_id.capitalize()} Agent Output", "", output]

    out_file.write_text("\n".join(front_matter + body) + "\n", encoding="utf-8")
    return out_file


async def _create_external_issues(
    *,
    task: str,
    agents: list[str],
    writer_output: str,
    project: str,
    push_paperclip: bool = True,
    push_multica: bool = False,
) -> tuple[dict | None, dict | None]:
    """Create issues on external platforms. Returns (paperclip_issue, multica_issue)."""
    parsed = _parse_writer_output(writer_output, task)
    assignee = AGENT_ASSIGNEES.get(agents[-1], "Hermes")

    paperclip_issue = None
    multica_issue = None

    if push_paperclip:
        pc = PaperclipAdapter()
        try:
            paperclip_issue = await pc.create_issue(
                title=parsed[0],
                description=parsed[1],
                project=project,
                assignee=assignee,
                labels=["war-room", "auto-generated"],
                priority="medium",
                acceptance_criteria=list(parsed[2]),
            )
            logger.info("📌 Paperclip issue: %s", paperclip_issue.get("id"))
        except Exception as e:
            logger.warning("Paperclip issue creation failed: %s", e)
        finally:
            await pc.close()

    if push_multica:
        mc = MulticaAdapter()
        try:
            multica_issue = await mc.create_issue(
                title=parsed[0],
                description=parsed[1],
                project=project,
                assignee=assignee,
            )
            logger.info("📌 Multica issue: %s", multica_issue.get("id"))
        except Exception as e:
            logger.warning("Multica issue creation failed: %s", e)
        finally:
            await mc.close()

    return paperclip_issue, multica_issue


def _parse_writer_output(writer_output: str, fallback_task: str) -> tuple[str, str, list[str]]:
    """Module-level version for external issue creation."""
    title = f"[War Room] {fallback_task[:70]}"
    description = writer_output[:1000] if writer_output else fallback_task
    ac: list[str] = []

    for line in writer_output.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("title:"):
            title = stripped[6:].strip()
        elif stripped.startswith("# "):
            title = stripped[2:].strip()
        if "- [ ]" in stripped:
            ac.append(stripped.replace("- [ ]", "").strip())

    return title, description, ac


def get_pipeline(engine: str = "native"):
    """Factory: return a pipeline instance by engine name."""
    if engine == "crewai":
        from war_room.pipelines.crewai_pipeline import CrewAIPipeline

        return CrewAIPipeline()
    return WarRoomPipeline()


async def cmd_run(
    task: str,
    agents_str: str | None = None,
    project: str = "NERVIX",
    with_coder: bool = False,
    engine: str = "native",
):
    if agents_str:
        agents = agents_str.split(",")
    elif with_coder:
        agents = ["research", "architect", "coder", "writer"]
    else:
        agents = None  # default
    pipeline = get_pipeline(engine)
    return await pipeline.run(task, agents=agents, project=project)
