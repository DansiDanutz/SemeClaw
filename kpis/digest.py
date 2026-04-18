"""
kpis/digest.py — Daily KPI digest at 18:00 EET.

Fetches today's KPIs from Supabase, formats a Telegram message,
and sends to Dan. Also flags stale/dead projects.

Usage (cron at 18:00 EET = 15:00 UTC):
  /opt/homebrew/bin/uv run python3 -m kpis.digest
"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from kpis.collector import get_today_kpis, get_projects_state, _today
from sentinel.alerts import send_alert

logger = logging.getLogger("kpis.digest")


HEALTH_ICONS = {
    "active": "✅",
    "stale":  "⚠️",
    "dead":   "🔴",
}


def _format_digest(kpis: list[dict], projects: list[dict]) -> str:
    day = _today()
    lines = [f"📊 *Daily KPIs — {day}*\n"]

    kpi_map = {k["project"]: k for k in kpis}
    proj_map = {p["project"]: p for p in projects}

    # All known projects in priority order
    priority = ["nervix", "zmartychat", "mywork", "paperclip", "semeclaw", "crawdbot"]
    shown = set()

    for project in priority:
        kpi  = kpi_map.get(project, {})
        proj = proj_map.get(project, {})
        _format_project_line(lines, project, kpi, proj)
        shown.add(project)

    # Any project with KPI data not in priority list
    for project, kpi in kpi_map.items():
        if project not in shown:
            proj = proj_map.get(project, {})
            _format_project_line(lines, project, kpi, proj)

    # Summary
    total_done   = sum(k.get("tasks_completed", 0) for k in kpis)
    total_failed = sum(k.get("tasks_failed", 0) for k in kpis)
    total_tok    = sum(k.get("tokens_spent", 0) for k in kpis)
    total_cost   = sum(float(k.get("cost_usd") or 0) for k in kpis)
    total_commits= sum(k.get("new_commits", 0) for k in kpis)

    lines.append(f"\n*Total:* {total_done} done / {total_failed} failed | "
                 f"{total_tok//1000}K tok | ${total_cost:.2f} | {total_commits} commits")

    # Dead project escalation
    dead = [p for p in projects if p.get("health") == "dead" or (
        p["project"] in kpi_map and kpi_map[p["project"]].get("tasks_completed", 0) == 0
        and kpi_map[p["project"]].get("tasks_failed", 0) == 0
    )]
    if dead:
        dead_names = ", ".join(p["project"] for p in dead[:5])
        lines.append(f"\n🔴 *Dead projects* (0 tasks today): {dead_names}")
        lines.append("Action required — assign tasks or escalate.")

    return "\n".join(lines)


def _format_project_line(lines: list, project: str, kpi: dict, proj: dict):
    done    = kpi.get("tasks_completed", 0)
    failed  = kpi.get("tasks_failed", 0)
    tok     = kpi.get("tokens_spent", 0)
    commits = kpi.get("new_commits", 0)
    evolved = kpi.get("evolved", False)
    deploys = kpi.get("deploys", 0)

    if done == 0 and failed == 0 and tok == 0:
        health = "dead"
    elif failed > done:
        health = "stale"
    else:
        health = "active"

    icon    = HEALTH_ICONS.get(health, "❓")
    ev_icon = "✅" if evolved else ("⚠️" if done > 0 else "🔴")
    agent   = kpi.get("owner_agent") or proj.get("owner_agent", "?")
    deploy_str = f" | {deploys}🚀" if deploys else ""

    lines.append(
        f"{icon} *{project:12}* {done:3}done/{failed}fail | "
        f"{tok//1000}K tok | {commits}💾{deploy_str} | {ev_icon} evolved [{agent}]"
    )


async def send_digest():
    kpis     = get_today_kpis()
    projects = get_projects_state()
    msg      = _format_digest(kpis, projects)
    await send_alert(msg, level="", dedup_key="kpi-digest-daily", dedup_sec=3600)
    logger.info("Daily digest sent")
    return msg


if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)
    msg = asyncio.run(send_digest())
    print(msg)
