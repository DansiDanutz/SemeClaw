"""
War Room — Orchestrator
Dan's Lab Agent Fleet coordinator.

Pipeline:
  User task → Research → Architect|Strategist → Writer → External issue(s)

Issues are pushed to:
  - Paperclip (default, always)
  - Multica (when reachable / configured)

Usage:
  python war_room/war_room.py run "Research open-source AI agent marketplaces"
  python war_room/war_room.py status
  python war_room/war_room.py board
  python war_room/war_room.py --help
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import litellm
import typer
from rich.console import Console
from rich.table import Table

# ---------------------------------------------------------------------------
# Bootstrap path so this runs from repo root
# ---------------------------------------------------------------------------
ROOT = Path(__file__).parent.parent
WAR_ROOM_DIR = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(WAR_ROOM_DIR))

from adapters.paperclip import PaperclipAdapter, AGENT_ASSIGNEES  # noqa: E402
from adapters.multica import MulticaAdapter  # noqa: E402
from adapters.base import AdapterHealth  # noqa: E402
from config import resolve_model, resolve_telegram_token  # noqa: E402
from parsing import parse_writer_output  # noqa: E402
from state import (  # noqa: E402
    atomic_write_json,
    record_completed_task,
    utc_now_iso,
)

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
AGENTS_DIR      = WAR_ROOM_DIR / "agents"
RESEARCH_DIR    = WAR_ROOM_DIR / "research"
LOGS_DIR        = WAR_ROOM_DIR / "logs"
STATE_FILE      = WAR_ROOM_DIR / "shared_state.json"
TASK_QUEUE_FILE = WAR_ROOM_DIR / "task_queue.json"
TELEGRAM_CHAT_FILE = ROOT / ".telegram_chat_id"

RESEARCH_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

DEFAULT_AGENTS = ("research", "strategist", "writer")


# ---------------------------------------------------------------------------
# Agent definitions (load from markdown files)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AgentDef:
    id: str
    system_prompt: str


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

async def call_agent(
    agent_id: str,
    task: str,
    context: str = "",
    model: str | None = None,
) -> str:
    """Call a War Room agent with a task. Returns the response text."""
    model = resolve_model(ROOT, model)
    agent = load_agent_def(agent_id)

    user_msg = task
    if context:
        user_msg = (
            f"## Context from previous agents\n\n{context}\n\n"
            f"---\n\n## Your Task\n\n{task}"
        )

    logger.info("🤖 Calling agent [%s] with model %s", agent_id, model)

    response = await litellm.acompletion(
        model=model,
        messages=[{"role": "user", "content": user_msg}],
        system=agent.system_prompt,
        max_tokens=4096,
        temperature=0.3,
    )
    result = response.choices[0].message.content or ""
    logger.info("✅ Agent [%s] responded (%d chars)", agent_id, len(result))
    return result


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
            "run_id":          self.run_id,
            "task":            self.task,
            "agents_run":      self.agents_run,
            "output_file":     str(self.output_file),
            "paperclip_issue": self.paperclip_issue,
            "multica_issue":   self.multica_issue,
            "results":         {k: v[:200] + "…" for k, v in self.results.items()},
        }


class WarRoomPipeline:
    """
    Orchestrates Research → Architect/Strategist → Writer
    and creates external issues from the Writer output.
    """

    def __init__(self, model: str | None = None):
        self.model = resolve_model(ROOT, model)
        self.results: dict[str, str] = {}

    async def run(
        self,
        task: str,
        agents: list[str] | None = None,
        project: str = "NERVIX",
        notify_telegram: bool = True,
        push_paperclip: bool = True,
        push_multica: bool = False,
    ) -> PipelineResult:
        agents = agents or list(DEFAULT_AGENTS)
        run_id = uuid.uuid4().hex[:8]
        started = utc_now_iso()

        logger.info("🚀 War Room pipeline started | task=%s | run_id=%s", task[:60], run_id)

        context = ""
        self.results = {}
        succeeded = True
        try:
            for agent_id in agents:
                logger.info("▶️  Running agent: %s", agent_id)
                output = await call_agent(agent_id, task, context=context, model=self.model)
                self.results[agent_id] = output
                context += f"\n\n## Output from {agent_id.capitalize()} Agent\n\n{output}"
        except Exception as e:
            succeeded = False
            logger.exception("Pipeline failed during agent run: %s", e)
            # Save whatever we have so the run isn't silently lost
            if not self.results:
                raise

        out_file = _write_report_file(task, agents, started, self.results)
        logger.info("📄 Saved report: %s", out_file.name)

        paperclip_issue = None
        multica_issue = None
        if succeeded:
            paperclip_issue, multica_issue = await _create_external_issues(
                task=task,
                agents=agents,
                writer_output=self.results.get("writer", ""),
                project=project,
                push_paperclip=push_paperclip,
                push_multica=push_multica,
            )

        if notify_telegram:
            await _telegram_notify(task, out_file, paperclip_issue, multica_issue)

        record_completed_task(
            STATE_FILE,
            run_id=run_id,
            task=task,
            agents=agents,
            issue_id=paperclip_issue.get("id") if paperclip_issue else None,
            multica_issue_id=multica_issue.get("id") if multica_issue else None,
            succeeded=succeeded,
        )
        _log_run(run_id, task, agents, started, paperclip_issue, multica_issue, succeeded)

        return PipelineResult(
            run_id=run_id,
            task=task,
            agents_run=agents,
            output_file=out_file,
            paperclip_issue=paperclip_issue,
            multica_issue=multica_issue,
            results=self.results,
        )


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
    parsed = parse_writer_output(writer_output, task)
    assignee = AGENT_ASSIGNEES.get(agents[-1], "Hermes")

    paperclip_issue = None
    multica_issue = None

    if push_paperclip:
        pc = PaperclipAdapter()
        try:
            paperclip_issue = await pc.create_issue(
                title=parsed.title,
                description=parsed.description,
                project=project,
                assignee=assignee,
                labels=["war-room", "research", "auto-generated"],
                priority="medium",
                acceptance_criteria=list(parsed.acceptance_criteria),
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
                title=parsed.title,
                description=parsed.description,
                assignee=assignee,
                priority="medium",
                status="backlog",
            )
            logger.info("📌 Multica issue: %s", multica_issue.get("id"))
        except Exception as e:
            logger.warning("Multica issue creation failed: %s", e)
        finally:
            await mc.close()

    return paperclip_issue, multica_issue


async def _telegram_notify(
    task: str,
    report_file: Path,
    paperclip_issue: dict | None,
    multica_issue: dict | None,
) -> None:
    if not TELEGRAM_CHAT_FILE.exists():
        return
    token = resolve_telegram_token(ROOT)
    if not token:
        return

    chat_id = TELEGRAM_CHAT_FILE.read_text(encoding="utf-8").strip()
    if not chat_id:
        return

    pc_id = paperclip_issue.get("id", "?") if paperclip_issue else "—"
    mc_id = multica_issue.get("id", "?") if multica_issue else "—"
    msg = (
        f"🏛 <b>War Room complete</b>\n"
        f"<b>Task:</b> {task[:80]}\n"
        f"<b>Paperclip:</b> {pc_id}\n"
        f"<b>Multica:</b> {mc_id}\n"
        f"<b>Report:</b> {report_file.name}"
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
            )
    except Exception as e:
        logger.warning("Telegram notify failed: %s", e)


def _log_run(
    run_id: str,
    task: str,
    agents: list[str],
    started: str,
    paperclip_issue: dict | None,
    multica_issue: dict | None,
    succeeded: bool,
) -> None:
    log_file = LOGS_DIR / f"run-{started[:10]}.jsonl"
    record = {
        "run_id":           run_id,
        "task":             task,
        "agents":           agents,
        "started":          started,
        "completed":        utc_now_iso(),
        "paperclip_issue_id": paperclip_issue.get("id") if paperclip_issue else None,
        "multica_issue_id":   multica_issue.get("id") if multica_issue else None,
        "succeeded":        succeeded,
    }
    import json as _json
    with log_file.open("a", encoding="utf-8") as f:
        f.write(_json.dumps(record) + "\n")


def _format_status(state: dict) -> None:
    metrics = state.get("metrics", {})
    completed = state.get("completed_tasks", [])

    console.print()
    console.print("[bold]🏛  War Room Status[/bold]")
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_row("Tasks run:",        str(metrics.get("tasks_run", 0)))
    table.add_row("Tasks succeeded:",  str(metrics.get("tasks_succeeded", 0)))
    table.add_row("Tasks failed:",     str(metrics.get("tasks_failed", 0)))
    table.add_row("Paperclip issues:", str(metrics.get("paperclip_issues_created", 0)))
    table.add_row("Multica issues:",   str(metrics.get("multica_issues_created", 0)))
    table.add_row("Last updated:",     state.get("last_updated") or "never")
    console.print(table)

    if completed:
        console.print("\n[bold]Recent tasks:[/bold]")
        recent = Table(show_header=True, box=None, pad_edge=False)
        recent.add_column("Paperclip", style="cyan", no_wrap=True)
        recent.add_column("Multica", style="magenta", no_wrap=True)
        recent.add_column("Task", overflow="fold")
        recent.add_column("Completed", style="dim", no_wrap=True)
        for t in completed[-5:]:
            recent.add_row(
                t.get("issue_id") or "—",
                t.get("multica_issue_id") or "—",
                (t.get("task") or "")[:60],
                (t.get("completed_at") or "")[:16],
            )
        console.print(recent)


def _format_board(board: dict, label: str = "Board") -> None:
    console.print()
    console.print(f"[bold]🏛  {label}[/bold]")
    console.print(f"Mode: [cyan]{board.get('mode', 'live')}[/cyan]")
    console.print(f"Projects: {', '.join(board.get('projects', [])) or '—'}")

    agents = board.get("agents", {})
    if agents:
        tbl = Table(title="\nAgents", show_header=True, box=None, pad_edge=False)
        tbl.add_column("Name", style="cyan")
        tbl.add_column("Role")
        tbl.add_column("Status", style="green")
        tbl.add_column("Open", justify="right")
        for name, info in agents.items():
            tbl.add_row(name, info.get("role", ""), info.get("status", ""),
                        str(info.get("open_issues", 0)))
        console.print(tbl)

    issues = board.get("issues", [])
    if issues:
        tbl = Table(title=f"\nIssues ({len(issues)})", show_header=True, box=None, pad_edge=False)
        tbl.add_column("ID", style="cyan", no_wrap=True)
        tbl.add_column("Title", overflow="fold")
        tbl.add_column("Assignee")
        for i in issues:
            tbl.add_row(i.get("id", "?"), (i.get("title") or "")[:55], i.get("assignee", "?"))
        console.print(tbl)


# ---------------------------------------------------------------------------
# Typer CLI
# ---------------------------------------------------------------------------

app = typer.Typer(
    add_completion=False,
    help="War Room — Dan's Lab Agent Fleet coordinator.",
    no_args_is_help=True,
)


@app.command("run", help="Run a full pipeline for a task.")
def cli_run(
    task: str = typer.Argument(..., help="Task description for the pipeline."),
    agents: str = typer.Option(
        ",".join(DEFAULT_AGENTS),
        "--agents",
        help="Comma-separated agent chain (e.g. research,strategist,writer).",
    ),
    project: str = typer.Option("NERVIX", "--project", help="Paperclip project name."),
    model: str | None = typer.Option(None, "--model", help="Override LLM model string."),
    no_telegram: bool = typer.Option(False, "--no-telegram", help="Skip Telegram notification."),
    no_paperclip: bool = typer.Option(False, "--no-paperclip", help="Skip Paperclip issue creation."),
    multica: bool = typer.Option(False, "--multica", help="Also push to Multica."),
) -> None:
    agent_list = [a.strip() for a in agents.split(",") if a.strip()]
    pipeline = WarRoomPipeline(model=model)
    result = asyncio.run(pipeline.run(
        task=task,
        agents=agent_list,
        project=project,
        notify_telegram=not no_telegram,
        push_paperclip=not no_paperclip,
        push_multica=multica,
    ))

    console.print()
    console.rule("[bold green]✅ War Room pipeline complete[/bold green]")
    console.print(f"Run ID:  [cyan]{result.run_id}[/cyan]")
    console.print(f"Report:  {result.output_file}")
    if result.paperclip_issue:
        issue = result.paperclip_issue
        console.print(
            f"Paperclip: [cyan]{issue['id']}[/cyan] → {issue['assignee']} ({issue['project']})"
        )
    if result.multica_issue:
        issue = result.multica_issue
        console.print(
            f"Multica:   [cyan]{issue['id']}[/cyan] → {issue.get('assignee', '?')}"
        )
    console.rule()


@app.command("status", help="Show War Room metrics and recent tasks.")
def cli_status() -> None:
    from state import load_state
    state = load_state(STATE_FILE)
    _format_status(state)


@app.command("board", help="Show live board state from all connected platforms.")
def cli_board() -> None:
    async def _go() -> None:
        pc = PaperclipAdapter()
        mc = MulticaAdapter()
        try:
            pc_board = await pc.get_board_state()
            _format_board(pc_board, label="Paperclip")
        except Exception as e:
            console.print(f"[red]Paperclip board failed: {e}[/red]")
        finally:
            await pc.close()

        try:
            mc_agents = await mc.list_agents()
            mc_tasks = await mc.list_tasks()
            # Synthesize a board-like view from Multica data
            mc_board = {
                "mode": "live" if mc._is_reachable() else "local",
                "projects": [],
                "agents": {
                    a["name"]: {"role": a.get("role", "Agent"), "status": a["status"], "open_issues": 0}
                    for a in mc_agents
                },
                "issues": mc_tasks,
            }
            if mc_board["agents"] or mc_board["issues"]:
                _format_board(mc_board, label="Multica")
            else:
                console.print("\n[yellow]Multica: no agents or issues found[/yellow]")
        except Exception as e:
            console.print(f"[red]Multica board failed: {e}[/red]")
        finally:
            await mc.close()

    asyncio.run(_go())


@app.command("enqueue", help="Queue a task for later processing.")
def cli_enqueue(
    task: str = typer.Argument(..., help="Task description."),
    project: str = typer.Option("NERVIX", "--project"),
) -> None:
    from state import enqueue_task
    enqueue_task(TASK_QUEUE_FILE, {"task": task, "project": project})
    console.print(f"[green]Queued:[/green] {task}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
