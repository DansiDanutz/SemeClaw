"""CrewAI-backed War Room pipeline.

Mirrors the native WarRoomPipeline interface so callers can swap engines
without changing dashboard, CLI, or meeting code.
"""

from __future__ import annotations

import json
import logging
import os

# Allow importing war_room modules directly
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

WAR_ROOM_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(WAR_ROOM_DIR))

from adapters.paperclip import PaperclipAdapter
from memory import WarRoomMemory
from paperclip_bridge import AGENT_ASSIGNEES

logger = logging.getLogger("war_room.crewai_pipeline")

# Re-use paths from war_room layout
RESEARCH_DIR = WAR_ROOM_DIR / "research"
LOGS_DIR = WAR_ROOM_DIR / "logs"
STATE_FILE = WAR_ROOM_DIR / "shared_state.json"
TELEGRAM_CHAT_FILE = WAR_ROOM_DIR.parent / ".telegram_chat_id"

RESEARCH_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# Re-use native model routing
AGENT_MODELS: dict[str, dict] = {
    "research": {
        "model": "openrouter/qwen/qwen3.6-plus:free",
        "api_base": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "api_key_fallback": "",
        "max_tokens": 8192,
        "temperature": 0.2,
    },
    "strategist": {
        "model": "kimi-k2.5",
        "api_base": "https://api.moonshot.ai/v1",
        "api_key_env": "MOONSHOT_API_KEY",
        "api_key_fallback": "",
        "max_tokens": 8192,
        "temperature": 0.3,
    },
    "architect": {
        "model": "glm-5",
        "api_base": "https://open.bigmodel.cn/api/paas/v4",
        "api_key_env": "ZHIPU_API_KEY",
        "api_key_fallback": "",
        "max_tokens": 8192,
        "temperature": 0.25,
    },
    "coder": {
        "model": "glm-5",
        "api_base": "https://open.bigmodel.cn/api/paas/v4",
        "api_key_env": "ZHIPU_API_KEY",
        "api_key_fallback": "",
        "max_tokens": 8192,
        "temperature": 0.2,
    },
    "writer": {
        "model": "gpt-5.4",
        "api_base": "http://127.0.0.1:8995/v1",
        "api_key_env": "OPENAI_API_KEY",
        "api_key_fallback": "sk-local",
        "max_tokens": 4096,
        "temperature": 0.4,
    },
    "narrator": {
        "model": "ollama/gemma4",
        "api_base": "http://127.0.0.1:11434",
        "api_key_env": None,
        "api_key_fallback": "ollama",
        "max_tokens": 2048,
        "temperature": 0.5,
    },
    "david": {
        "model": "ollama/gemma4",
        "api_base": "http://127.0.0.1:11434",
        "api_key_env": None,
        "api_key_fallback": "ollama",
        "max_tokens": 2048,
        "temperature": 0.4,
    },
}

_FALLBACK_MODEL_CFG = {
    "model": "openrouter/qwen/qwen3.6-plus:free",
    "api_base": "https://openrouter.ai/api/v1",
    "api_key_env": "OPENROUTER_API_KEY",
    "api_key_fallback": "",
    "max_tokens": 4096,
    "temperature": 0.3,
}


def _get_model_cfg(agent_id: str) -> dict[str, Any]:
    cfg = dict(AGENT_MODELS.get(agent_id, _FALLBACK_MODEL_CFG))
    key_env = cfg.get("api_key_env")
    if key_env:
        cfg["api_key"] = os.environ.get(key_env) or cfg.get("api_key_fallback", "")
    else:
        cfg["api_key"] = cfg.get("api_key_fallback", "ollama")
    return cfg


def _load_telegram_creds() -> tuple[str | None, str | None]:
    bot_token = None
    chat_id = None
    cfg_path = WAR_ROOM_DIR.parent / "default_workspace" / "config.yaml"
    if cfg_path.exists():
        try:
            import yaml

            cfg = yaml.safe_load(cfg_path.read_text())
            tg = cfg.get("telegram", {})
            bot_token = tg.get("bot_token") or tg.get("token")
            chat_id = tg.get("chat_id") or tg.get("dan_chat_id")
        except Exception:
            pass
    chat_id_file = WAR_ROOM_DIR.parent / ".telegram_chat_id"
    if not chat_id and chat_id_file.exists():
        chat_id = chat_id_file.read_text().strip()
    if not bot_token:
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("DLS_TELEGRAM_BOT_TOKEN")
    if not chat_id:
        chat_id = os.environ.get("DLS_DAN_CHAT_ID")
    return bot_token, chat_id


def _slugify(value: str, max_len: int = 40) -> str:
    cleaned = "".join(c if c.isalnum() else "-" for c in value.lower())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")[:max_len].strip("-") or "task"


def _build_report(task: str, agents: list[str], started: str, results: dict[str, str]) -> str:
    lines = [
        "# War Room Report",
        f"**Task:** {task}",
        f"**Date:** {started[:10]}",
        f"**Agents:** {' → '.join(a.capitalize() for a in agents)}",
        "",
    ]
    for agent_id in agents:
        output = results.get(agent_id, "")
        lines += [f"\n---\n\n## {agent_id.capitalize()} Agent Output\n\n{output}"]
    return "\n".join(lines)


def _build_memory_summary(task: str, results: dict[str, str]) -> str:
    parts = [f"Task: {task}"]
    for agent_id in ["research", "strategist", "architect", "writer", "coder"]:
        if agent_id in results:
            parts.append(f"\n[{agent_id.capitalize()}]:\n{results[agent_id][:500]}")
    return "\n".join(parts)


def _parse_writer_output(writer_output: str, fallback_task: str) -> tuple[str, str, list[str]]:
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


class CrewAIPipeline:
    """Drop-in replacement for WarRoomPipeline using CrewAI orchestration."""

    def __init__(self, process: str = "sequential"):
        self.process = process
        self.results: dict[str, str] = {}
        self.memory = WarRoomMemory(WAR_ROOM_DIR / "memory")

    async def run(
        self,
        task: str,
        agents: list[str] | None = None,
        project: str = "NERVIX",
        notify_telegram: bool = True,
    ) -> Any:
        agents = agents or ["research", "strategist", "writer"]
        run_id = str(uuid.uuid4())[:8]
        started = datetime.now(timezone.utc).isoformat()

        logger.info("🚀 CrewAI pipeline | run_id=%s | agents=%s", run_id, " → ".join(agents))
        logger.info("   Task: %s", task[:80])

        prior_memory = self.memory.load_relevant(task)
        if prior_memory:
            logger.info("🧠 Loaded %d prior memory entries for context", len(prior_memory))

        context = ""
        if prior_memory:
            mem_lines = ["## Prior Research from Memory\n"]
            for entry in prior_memory:
                mem_lines.append(
                    f"### [{entry['date']}] {entry['topic']}\n{entry['summary']}\n"
                    f"_(Full report: {entry.get('file', 'n/a')})_\n"
                )
            context = "\n".join(mem_lines)

        model_cfgs = {aid: _get_model_cfg(aid) for aid in agents}

        from semeclaw.integrations.crewai_runner import run_crewai_pipeline

        self.results = await run_crewai_pipeline(
            task=task,
            agents=agents,
            model_cfgs=model_cfgs,
            process=self.process,
            initial_context=context,
        )

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        slug = _slugify(task)
        out_file = RESEARCH_DIR / f"{slug}-{date_str}.md"
        out_file.write_text(_build_report(task, agents, started, self.results), encoding="utf-8")
        logger.info("📄 Saved report: %s", out_file.name)

        self.memory.save(
            topic=task[:80],
            summary=_build_memory_summary(task, self.results),
            run_id=run_id,
            file=str(out_file),
        )
        logger.info("🧠 Saved to memory")

        paperclip_issue = None
        multica_issue = None
        try:
            writer_output = self.results.get("writer", "")
            title, description, ac = _parse_writer_output(writer_output, task)
            assignee = AGENT_ASSIGNEES.get(agents[-1], "Hermes")
            pc = PaperclipAdapter()
            try:
                paperclip_issue = await pc.create_issue(
                    title=title,
                    description=description,
                    project=project,
                    assignee=assignee,
                    labels=["war-room", "auto-generated", "crewai"],
                    priority="medium",
                    acceptance_criteria=ac,
                )
                logger.info("📌 Paperclip issue: %s", paperclip_issue.get("id"))
            finally:
                await pc.close()
        except Exception as e:
            logger.warning("Paperclip issue creation failed: %s", e)

        if notify_telegram:
            await self._telegram_notify(task, out_file, paperclip_issue, agents)

        self._update_state(run_id, task, agents, paperclip_issue)
        self._log_run(run_id, task, agents, started, paperclip_issue)

        # Return a duck-typed PipelineResult-like object
        class _Result:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)

            def to_public(self):
                return {
                    "run_id": self.run_id,
                    "task": self.task,
                    "agents_run": self.agents_run,
                    "output_file": str(self.output_file),
                    "paperclip_issue": self.paperclip_issue,
                    "multica_issue": self.multica_issue,
                    "results": {k: v[:200] + "…" for k, v in self.results.items()},
                }

        return _Result(
            run_id=run_id,
            task=task,
            agents_run=agents,
            output_file=out_file,
            paperclip_issue=paperclip_issue,
            multica_issue=multica_issue,
            results=self.results,
        )

    async def _telegram_notify(
        self,
        task: str,
        report_file: Path,
        issue: dict | None,
        agents: list[str],
    ):
        bot_token, chat_id = _load_telegram_creds()
        if not bot_token or not chat_id:
            logger.debug("Telegram notify skipped — no credentials found")
            return

        issue_id = issue.get("id", "?") if issue else "?"
        pipeline_str = " → ".join(a.capitalize() for a in agents)
        msg = (
            f"🏛 <b>CrewAI War Room complete</b>\n"
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
                "engine": "crewai",
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
            "engine": "crewai",
        }
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
