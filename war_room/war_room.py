"""
War Room — Orchestrator
Dan's Lab Paperclip Agent Fleet coordinator.

Pipeline:
  User task → Research → Architect|Strategist → Writer → Paperclip issue

Usage:
  python war_room/war_room.py run "Research open-source AI agent marketplaces"
  python war_room/war_room.py status
  python war_room/war_room.py board
"""

import asyncio
import json
import logging
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
import litellm

# ---------------------------------------------------------------------------
# Bootstrap path so this runs from repo root
# ---------------------------------------------------------------------------
ROOT = Path(__file__).parent.parent
WAR_ROOM_DIR_BOOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(WAR_ROOM_DIR_BOOT))  # so paperclip_bridge is importable directly

from paperclip_bridge import PaperclipBridge, AGENT_ASSIGNEES, load_bridge
from research_tools import ResearchTools

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("war_room")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
WAR_ROOM_DIR      = Path(__file__).parent
AGENTS_DIR        = WAR_ROOM_DIR / "agents"
RESEARCH_DIR      = WAR_ROOM_DIR / "research"
LOGS_DIR          = WAR_ROOM_DIR / "logs"
STATE_FILE        = WAR_ROOM_DIR / "shared_state.json"
TASK_QUEUE_FILE   = WAR_ROOM_DIR / "task_queue.json"

RESEARCH_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# LLM config (reads from SemeClaw workspace config if available)
# ---------------------------------------------------------------------------
DEFAULT_MODEL = "dashscope/qwen3.6-plus"

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


# ---------------------------------------------------------------------------
# Agent definitions (load from markdown files)
# ---------------------------------------------------------------------------

def load_agent_def(agent_id: str) -> dict:
    """Load agent markdown file and return {id, name, system_prompt}."""
    path = AGENTS_DIR / f"{agent_id}.md"
    if not path.exists():
        raise FileNotFoundError(f"Agent def not found: {path}")
    text = path.read_text(encoding="utf-8")
    # Strip YAML front-matter
    if text.startswith("---"):
        parts = text.split("---", 2)
        body = parts[2].strip() if len(parts) >= 3 else text
    else:
        body = text
    return {"id": agent_id, "system_prompt": body}


# ---------------------------------------------------------------------------
# Single agent call
# ---------------------------------------------------------------------------

async def call_agent(
    agent_id: str,
    task: str,
    context: str = "",
    model: str | None = None,
    tools: ResearchTools | None = None,
) -> str:
    """
    Call a War Room agent with a task.
    Returns the agent's response as a string.
    """
    model = model or _load_model()
    agent = load_agent_def(agent_id)

    system = agent["system_prompt"]
    user_msg = task
    if context:
        user_msg = f"## Context from previous agents\n\n{context}\n\n---\n\n## Your Task\n\n{task}"

    logger.info("🤖 Calling agent [%s] with model %s", agent_id, model)

    # For research agent, inject tools description and do tool-use loop
    if agent_id == "research" and tools:
        system += tools.get_available_tools_description()

    messages = [{"role": "user", "content": user_msg}]

    DASHSCOPE_INTL_BASE = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

    response = await litellm.acompletion(
        model=model,
        messages=messages,
        system=system,
        max_tokens=4096,
        temperature=0.3,
        api_base=DASHSCOPE_INTL_BASE,
    )
    result = response.choices[0].message.content or ""

    # Tool-use loop for research agent
    if agent_id == "research" and tools:
        tool_iterations = 0
        max_tool_iterations = 5
        while tool_iterations < max_tool_iterations:
            # Check if the response contains tool use instructions
            tool_call = _parse_tool_call(result, tools)
            if not tool_call:
                break

            tool_name, tool_args = tool_call
            logger.info("🔧 Research agent using tool: %s(%s)", tool_name, tool_args[:80])

            # Execute the tool
            if tool_name == "search":
                tool_result = await tools.search(tool_args.strip('"').strip("'"))
            elif tool_name == "extract":
                # Parse URLs from args
                import re
                urls = re.findall(r'https?://[^\s"\']+', tool_args)
                if urls:
                    tool_result = await tools.extract(urls)
                else:
                    tool_result = "[No URLs found in extract request]"
            elif tool_name == "browser_navigate":
                url = tool_args.strip('"').strip("'").strip()
                tool_result = await tools.browser_navigate(url)
            elif tool_name == "run_code":
                tool_result = await tools.run_code(tool_args)
            elif tool_name == "run_shell":
                tool_result = await tools.run_shell(tool_args)
            else:
                tool_result = f"[Unknown tool: {tool_name}]"

            # Feed tool result back to the model
            messages.append({"role": "assistant", "content": result})
            messages.append({
                "role": "user",
                "content": f"Tool result from {tool_name}:\n\n{tool_result[:4000]}\n\nContinue your research with this information.",
            })

            response = await litellm.acompletion(
                model=model,
                messages=messages,
                system=system,
                max_tokens=4096,
                temperature=0.3,
                api_base=DASHSCOPE_INTL_BASE,
            )
            result = response.choices[0].message.content or ""
            tool_iterations += 1
            logger.info("✅ Tool iteration %d/%d complete", tool_iterations, max_tool_iterations)

    logger.info("✅ Agent [%s] responded (%d chars)", agent_id, len(result))
    return result


def _parse_tool_call(response: str, tools: ResearchTools) -> tuple[str, str] | None:
    """
    Parse tool use from agent response.
    Looks for patterns like: search("query"), extract(["url1", "url2"]), etc.
    """
    import re

    tool_patterns = [
        (r'search\(["\'](.+?)["\']\)', 'search'),
        (r'extract\((.+?)\)', 'extract'),
        (r'browser_navigate\(["\'](.+?)["\']\)', 'browser_navigate'),
        (r'run_code\((.+?)\)', 'run_code'),
        (r'run_shell\((.+?)\)', 'run_shell'),
    ]

    for pattern, tool_name in tool_patterns:
        match = re.search(pattern, response, re.DOTALL)
        if match:
            return tool_name, match.group(1)

    # Also check for XML-style tool calls: <tool>search</tool><args>query</args>
    xml_tool = re.search(r'<tool>(\w+)</tool>\s*<args>(.+?)</args>', response, re.DOTALL)
    if xml_tool:
        return xml_tool.group(1), xml_tool.group(2)

    return None


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class WarRoomPipeline:
    """
    Orchestrates a full Research → Architect/Strategist → Writer pipeline
    and creates a Paperclip issue from the output.
    """

    def __init__(self, model: str | None = None):
        self.model = model or _load_model()
        self.results: dict[str, str] = {}

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
        started = datetime.utcnow().isoformat()

        logger.info("🚀 War Room pipeline started | task=%s | run_id=%s", task[:60], run_id)

        context = ""
        self.results = {}
        research_tools = ResearchTools()

        for agent_id in agents:
            logger.info("▶️  Running agent: %s", agent_id)
            output = await call_agent(agent_id, task, context=context, model=self.model, tools=research_tools)
            self.results[agent_id] = output
            context += f"\n\n## Output from {agent_id.capitalize()} Agent\n\n{output}"

        # Save all outputs to research dir
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        slug = task[:40].lower().replace(" ", "-").replace("/", "-")
        out_file = RESEARCH_DIR / f"{slug}-{date_str}.md"
        out_file.write_text(self._build_report(task, agents, started), encoding="utf-8")
        logger.info("📄 Saved report: %s", out_file.name)

        # Create Paperclip issue from writer output
        paperclip_issue = None
        bridge = load_bridge()
        async with bridge:
            writer_output = self.results.get("writer", "")
            title, description, ac = self._parse_writer_output(writer_output, task)
            assignee = AGENT_ASSIGNEES.get(agents[-1], "Hermes")

            paperclip_issue = await bridge.create_issue(
                title=title,
                description=description,
                project=project,
                assignee=assignee,
                labels=["war-room", "research", "auto-generated"],
                priority="medium",
                acceptance_criteria=ac,
            )
            logger.info("📌 Paperclip issue: %s", paperclip_issue.get("id"))

        # Notify via Telegram if available
        if notify_telegram:
            await self._telegram_notify(task, out_file, paperclip_issue)

        # Update shared state
        self._update_state(run_id, task, agents, paperclip_issue)

        # Log run
        self._log_run(run_id, task, agents, started, paperclip_issue)

        return {
            "run_id":         run_id,
            "task":           task,
            "agents_run":     agents,
            "output_file":    str(out_file),
            "paperclip_issue": paperclip_issue,
            "results":        {k: v[:200] + "…" for k, v in self.results.items()},
        }

    # ------------------------------------------------------------------ #
    #  Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _build_report(self, task: str, agents: list[str], started: str) -> str:
        lines = [
            f"# War Room Report",
            f"**Task:** {task}",
            f"**Date:** {started[:10]}",
            f"**Agents:** {' → '.join(a.capitalize() for a in agents)}",
            "",
        ]
        for agent_id in agents:
            output = self.results.get(agent_id, "")
            lines += [f"\n---\n\n## {agent_id.capitalize()} Agent Output\n\n{output}"]
        return "\n".join(lines)

    def _parse_writer_output(self, writer_output: str, fallback_task: str) -> tuple[str, str, list[str]]:
        """
        Try to extract a Paperclip issue title/description/AC from Writer output.
        Falls back to task string if not structured.
        """
        title = f"[War Room] {fallback_task[:70]}"
        description = writer_output[:1000] if writer_output else fallback_task
        ac: list[str] = []

        # Try to find a "Title:" line in the writer output
        for line in writer_output.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("title:"):
                title = stripped[6:].strip()
            if "- [ ]" in stripped:
                ac.append(stripped.replace("- [ ]", "").strip())

        return title, description, ac

    async def _telegram_notify(self, task: str, report_file: Path, issue: dict | None):
        """Send Telegram notification if .telegram_chat_id exists."""
        chat_id_file = ROOT / ".telegram_chat_id"
        if not chat_id_file.exists():
            return

        cfg_path = ROOT / "default_workspace" / "config.yaml"
        bot_token = None
        if cfg_path.exists():
            try:
                import yaml
                cfg = yaml.safe_load(cfg_path.read_text())
                bot_token = cfg.get("telegram", {}).get("bot_token")
            except Exception:
                pass

        if not bot_token:
            return

        chat_id = chat_id_file.read_text().strip()
        issue_id = issue.get("id", "?") if issue else "?"
        msg = (
            f"🏛 <b>War Room complete</b>\n"
            f"<b>Task:</b> {task[:80]}\n"
            f"<b>Paperclip:</b> {issue_id}\n"
            f"<b>Report:</b> {report_file.name}"
        )
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
                    timeout=10,
                )
        except Exception as e:
            logger.warning("Telegram notify failed: %s", e)

    def _update_state(self, run_id: str, task: str, agents: list[str], issue: dict | None):
        try:
            state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
            state.setdefault("completed_tasks", [])
            state.setdefault("metrics", {"tasks_run": 0, "tasks_succeeded": 0, "paperclip_issues_created": 0})
            state["completed_tasks"].append({
                "run_id":  run_id,
                "task":    task,
                "agents":  agents,
                "issue_id": issue.get("id") if issue else None,
                "completed_at": datetime.utcnow().isoformat(),
            })
            state["metrics"]["tasks_run"] += 1
            state["metrics"]["tasks_succeeded"] += 1
            if issue:
                state["metrics"]["paperclip_issues_created"] += 1
            state["last_updated"] = datetime.utcnow().isoformat()
            STATE_FILE.write_text(json.dumps(state, indent=2))
        except Exception as e:
            logger.warning("State update failed: %s", e)

    def _log_run(self, run_id: str, task: str, agents: list[str], started: str, issue: dict | None):
        log_file = LOGS_DIR / f"run-{datetime.utcnow().strftime('%Y-%m-%d')}.jsonl"
        record = {
            "run_id":    run_id,
            "task":      task,
            "agents":    agents,
            "started":   started,
            "completed": datetime.utcnow().isoformat(),
            "issue_id":  issue.get("id") if issue else None,
        }
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

async def cmd_run(task: str, agents_str: str | None = None, project: str = "NERVIX"):
    agents = agents_str.split(",") if agents_str else None
    pipeline = WarRoomPipeline()
    result = await pipeline.run(task, agents=agents, project=project)
    print("\n" + "=" * 60)
    print("✅ War Room pipeline complete")
    print(f"   Run ID:  {result['run_id']}")
    print(f"   Report:  {result['output_file']}")
    if result.get("paperclip_issue"):
        issue = result["paperclip_issue"]
        print(f"   Issue:   {issue['id']} → {issue['assignee']} ({issue['project']})")
    print("=" * 60)


async def cmd_status():
    state = {}
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text())
    metrics = state.get("metrics", {})
    completed = state.get("completed_tasks", [])
    print("\n🏛  War Room Status")
    print(f"   Tasks run:       {metrics.get('tasks_run', 0)}")
    print(f"   Tasks succeeded: {metrics.get('tasks_succeeded', 0)}")
    print(f"   Paperclip issues:{metrics.get('paperclip_issues_created', 0)}")
    print(f"   Last updated:    {state.get('last_updated', 'never')}")
    if completed:
        print("\n   Recent tasks:")
        for t in completed[-5:]:
            print(f"     [{t.get('issue_id','?')}] {t['task'][:60]} ({t['completed_at'][:10]})")


async def cmd_board():
    bridge = load_bridge()
    async with bridge:
        board = await bridge.get_board_state()
    print("\n🏛  Paperclip Board")
    print(f"   Mode: {board.get('mode', 'live')}")
    print(f"   Projects: {', '.join(board.get('projects', []))}")
    print("\n   Agents:")
    for name, info in board.get("agents", {}).items():
        print(f"     {name:8s} — {info['role']:14s} [{info['status']}]  open: {info.get('open_issues', 0)}")
    issues = board.get("issues", [])
    if issues:
        print(f"\n   Issues ({len(issues)}):")
        for i in issues:
            print(f"     [{i['id']}] {i['title'][:55]} → {i['assignee']}")


def main():
    import sys
    args = sys.argv[1:]
    if not args:
        print("Usage: war_room.py <run|status|board> [task] [--agents=a,b,c] [--project=NERVIX]")
        sys.exit(1)

    cmd = args[0]

    if cmd == "run":
        task = args[1] if len(args) > 1 else "Research AI agent marketplace landscape"
        agents_str = None
        project = "NERVIX"
        for a in args[2:]:
            if a.startswith("--agents="):
                agents_str = a.split("=", 1)[1]
            elif a.startswith("--project="):
                project = a.split("=", 1)[1]
        asyncio.run(cmd_run(task, agents_str, project))

    elif cmd == "status":
        asyncio.run(cmd_status())

    elif cmd == "board":
        asyncio.run(cmd_board())

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
