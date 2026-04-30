"""CrewAI adapter for SemeClaw.

Maps SemeClaw agent definitions (war_room/agents/*.md) to CrewAI Agent/Task objects,
and runs multi-agent pipelines via CrewAI's Crew/Process orchestration.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None  # type: ignore

logger = logging.getLogger(__name__)

DEFAULT_AGENTS_DIR = Path(__file__).parent.parent.parent.parent / "war_room" / "agents"


def _parse_agent_md(agent_id: str, agents_dir: Path | None = None) -> tuple[dict[str, Any], str]:
    """Parse a War Room agent markdown file into (frontmatter_dict, body_text)."""
    agents_dir = agents_dir or DEFAULT_AGENTS_DIR
    path = agents_dir / f"{agent_id}.md"
    if not path.exists():
        raise FileNotFoundError(f"Agent def not found: {path}")

    text = path.read_text(encoding="utf-8")
    frontmatter: dict[str, Any] = {}
    body = text

    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            raw_yaml = parts[1].strip()
            body = parts[2].strip()
            if yaml:
                try:
                    frontmatter = yaml.safe_load(raw_yaml) or {}
                except Exception as e:
                    logger.warning("Failed to parse YAML frontmatter for %s: %s", agent_id, e)

    return frontmatter, body


def _extract_goal_and_backstory(body: str, agent_id: str) -> tuple[str, str]:
    """Extract a concise goal + backstory from the markdown body."""
    lines = [ln for ln in body.splitlines() if ln.strip()]
    goal = f"Execute tasks as the {agent_id} agent."
    backstory = body[:3000]

    # Heuristic: look for "System Prompt" or "Role & Expertise" sections
    for i, line in enumerate(lines):
        low = line.lower()
        if "system prompt" in low or "role & expertise" in low or "## role" in low:
            # Collect next few non-empty lines as goal
            goal_lines = []
            for j in range(i + 1, min(i + 6, len(lines))):
                if lines[j].startswith("##"):
                    break
                goal_lines.append(lines[j].strip())
            if goal_lines:
                goal = " ".join(goal_lines)[:500]
            break

    return goal, backstory


def build_crewai_agent(agent_id: str, model_cfg: dict[str, Any] | None = None) -> Any:
    """Build a CrewAI Agent from a SemeClaw agent definition.

    Args:
        agent_id: e.g. "research", "strategist", "writer"
        model_cfg: dict with model, api_base, api_key, temperature, max_tokens
    """
    from crewai import Agent, LLM

    frontmatter, body = _parse_agent_md(agent_id)
    role = frontmatter.get("role") or frontmatter.get("name") or agent_id.capitalize()
    goal, backstory = _extract_goal_and_backstory(body, agent_id)

    mcfg = model_cfg or {}
    llm_kwargs: dict[str, Any] = {
        "model": mcfg.get("model", "openrouter/qwen/qwen3.6-plus:free"),
        "temperature": mcfg.get("temperature", 0.3),
        "max_tokens": mcfg.get("max_tokens", 4096),
    }
    api_key = mcfg.get("api_key")
    if api_key and api_key not in ("ollama", "sk-placeholder"):
        llm_kwargs["api_key"] = api_key
    api_base = mcfg.get("api_base")
    if api_base:
        llm_kwargs["base_url"] = api_base  # CrewAI uses base_url

    return Agent(
        role=role,
        goal=goal,
        backstory=backstory,
        llm=LLM(**llm_kwargs),
        verbose=True,
        allow_delegation=False,
    )


def build_crewai_task(
    agent: Any,
    agent_id: str,
    original_task: str,
    initial_context: str = "",
) -> Any:
    """Build a CrewAI Task for a given agent.

    In sequential mode CrewAI automatically chains outputs, so we only need
    to describe what THIS agent should do.
    """
    from crewai import Task

    description = f"""Your mission as the {agent_id} agent:

{original_task}

Use your expertise to produce a thorough, actionable response.
Format your output as markdown.
"""
    if initial_context:
        description += f"\n\n## Prior Context\n\n{initial_context[:2000]}\n"

    expected_output = (
        f"A detailed markdown output from the {agent_id} agent "
        f"covering all aspects of the task."
    )

    return Task(
        description=description,
        expected_output=expected_output,
        agent=agent,
    )


async def run_crewai_pipeline(
    task: str,
    agents: list[str],
    model_cfgs: dict[str, dict[str, Any]] | None = None,
    process: str = "sequential",
    initial_context: str = "",
) -> dict[str, str]:
    """Run a CrewAI pipeline and return per-agent outputs.

    Args:
        task: The overall task/prompt.
        agents: Ordered list of agent IDs.
        model_cfgs: Optional mapping of agent_id -> model config dict.
        process: "sequential" or "hierarchical".
        initial_context: Context string to prepend to the first task.

    Returns:
        dict mapping agent_id -> output string.
    """
    from crewai import Crew, Process

    model_cfgs = model_cfgs or {}
    crew_agents = []
    crew_tasks = []
    agent_id_map: list[str] = []

    for agent_id in agents:
        agent = build_crewai_agent(agent_id, model_cfgs.get(agent_id))
        crew_agents.append(agent)
        task_obj = build_crewai_task(
            agent=agent,
            agent_id=agent_id,
            original_task=task,
            initial_context=initial_context if not crew_tasks else "",
        )
        crew_tasks.append(task_obj)
        agent_id_map.append(agent_id)

    process_enum = Process.sequential if process == "sequential" else Process.hierarchical

    crew = Crew(
        agents=crew_agents,
        tasks=crew_tasks,
        process=process_enum,
        verbose=True,
    )

    logger.info("🚀 Starting CrewAI pipeline | agents=%s | process=%s", " → ".join(agents), process)

    await crew.kickoff_async()

    results: dict[str, str] = {}
    for idx, task_obj in enumerate(crew_tasks):
        aid = agent_id_map[idx]
        output = task_obj.output
        if output is None:
            output = ""
        results[aid] = str(output)
        logger.info("✅ [%s] done (%d chars)", aid, len(results[aid]))

    return results
