"""Experiment tracking and Telegram notification tools for SemeClaw.

Powers the daily autonomous research loop:
- log_experiment: record a research finding with before/after metric
- get_experiment_summary: pull today's or all-time results
- telegram_notify: send a message directly to Dan's Telegram (for cron reports)
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from pathlib import Path
from typing import Any

from semeclaw.tools.base import BaseTool


class LogExperimentTool(BaseTool):
    """Log a research experiment or finding to the results tracker."""

    def __init__(self, workspace_path: Path):
        super().__init__(
            name="log_experiment",
            description=(
                "Log a research experiment, test, or finding to the results tracker. "
                "Use this every time you research something and reach a conclusion. "
                "Each entry captures what you tried, what you found, and whether it "
                "was an improvement over the previous state."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Short title for this experiment or finding",
                    },
                    "category": {
                        "type": "string",
                        "enum": [
                            "market-research",
                            "competitor",
                            "ai-trends",
                            "nervix",
                            "technical",
                            "strategy",
                            "other",
                        ],
                        "description": "Category of research",
                    },
                    "hypothesis": {
                        "type": "string",
                        "description": "What you set out to investigate or test",
                    },
                    "finding": {
                        "type": "string",
                        "description": "What you actually found — the key insight or result",
                    },
                    "impact": {
                        "type": "string",
                        "enum": ["high", "medium", "low", "negative"],
                        "description": "How significant is this finding for NERVIX/DansLab",
                    },
                    "action": {
                        "type": "string",
                        "description": "Recommended action or next step based on this finding",
                    },
                },
                "required": ["title", "category", "hypothesis", "finding", "impact"],
            },
        )
        self.results_path = workspace_path / "research" / "experiments"
        self.results_path.mkdir(parents=True, exist_ok=True)
        self.tsv_file = self.results_path / "results.tsv"

    async def execute(self, args: dict[str, Any], session: Any) -> str:
        timestamp = datetime.now()
        date_str = timestamp.strftime("%Y-%m-%d")
        time_str = timestamp.strftime("%H:%M")

        row = {
            "date": date_str,
            "time": time_str,
            "title": args["title"],
            "category": args["category"],
            "hypothesis": args["hypothesis"],
            "finding": args["finding"],
            "impact": args["impact"],
            "action": args.get("action", ""),
        }

        # Append to TSV
        file_exists = self.tsv_file.exists()
        with open(self.tsv_file, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(row.keys()), delimiter="\t")
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)

        # Also save a detailed markdown entry in daily file
        daily_file = self.results_path / f"{date_str}.md"
        entry = f"\n## [{args['impact'].upper()}] {args['title']}\n"
        entry += f"*{time_str} | {args['category']}*\n\n"
        entry += f"**Hypothesis:** {args['hypothesis']}\n\n"
        entry += f"**Finding:** {args['finding']}\n\n"
        if args.get("action"):
            entry += f"**Action:** {args['action']}\n\n"

        if daily_file.exists():
            daily_file.write_text(daily_file.read_text() + entry)
        else:
            daily_file.write_text(f"# Research Log — {date_str}\n" + entry)

        return f"✓ Logged [{args['impact'].upper()}] {args['title']}"


class GetExperimentSummaryTool(BaseTool):
    """Read the experiment results and generate a summary."""

    def __init__(self, workspace_path: Path):
        super().__init__(
            name="get_experiment_summary",
            description=(
                "Get a summary of research experiments. "
                "Use 'today' for today's findings, 'week' for last 7 days, "
                "'all' for everything. Used to compile daily reports."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "enum": ["today", "week", "all"],
                        "description": "Time period to summarise",
                    },
                },
                "required": ["period"],
            },
        )
        self.results_path = workspace_path / "research" / "experiments"
        self.tsv_file = self.results_path / "results.tsv"

    async def execute(self, args: dict[str, Any], session: Any) -> str:
        if not self.tsv_file.exists():
            return "No experiments logged yet."

        period = args["period"]
        today = datetime.now().date()

        rows = []
        with open(self.tsv_file, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                try:
                    row_date = datetime.strptime(row["date"], "%Y-%m-%d").date()
                    if period == "today" and row_date != today:
                        continue
                    if period == "week" and (today - row_date).days > 7:
                        continue
                    rows.append(row)
                except Exception:
                    continue

        if not rows:
            return f"No experiments found for period: {period}"

        # Group by impact
        by_impact: dict[str, list] = {"high": [], "medium": [], "low": [], "negative": []}
        for row in rows:
            by_impact.setdefault(row.get("impact", "low"), []).append(row)

        lines = [f"## Research Summary ({period}) — {len(rows)} findings\n"]

        for impact in ["high", "medium", "low", "negative"]:
            group = by_impact.get(impact, [])
            if not group:
                continue
            emoji = {"high": "🔥", "medium": "✅", "low": "📝", "negative": "❌"}.get(impact, "•")
            lines.append(f"\n### {emoji} {impact.title()} Impact ({len(group)})\n")
            for r in group:
                lines.append(f"- **{r['title']}** ({r['category']})")
                lines.append(f"  _{r['finding'][:120]}_")
                if r.get("action"):
                    lines.append(f"  → {r['action']}")

        return "\n".join(lines)


class TelegramNotifyTool(BaseTool):
    """Send a Telegram message directly to Dan — for cron reports and alerts."""

    def __init__(self, workspace_path: Path):
        super().__init__(
            name="telegram_notify",
            description=(
                "Send a Telegram message directly to Dan. "
                "Use this at the END of cron jobs to deliver reports, summaries, "
                "or alerts. Supports Markdown formatting."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "The message to send. Use **bold**, - bullets, ## headers.",
                    },
                },
                "required": ["message"],
            },
        )
        self.workspace_path = workspace_path

    async def execute(self, args: dict[str, Any], session: Any) -> str:
        import httpx

        config = session.agent.config

        # Get bot token
        if not config.channels or not config.channels.telegram:
            return "Error: Telegram not configured"

        bot_token = config.channels.telegram.bot_token
        notify_chat_id = getattr(config.channels.telegram, "notify_chat_id", None)

        if not notify_chat_id:
            # Try to load from saved file
            chat_id_file = self.workspace_path / ".telegram_chat_id"
            if chat_id_file.exists():
                notify_chat_id = chat_id_file.read_text().strip()

        if not notify_chat_id:
            return (
                "Error: notify_chat_id not configured. "
                "Send a message to the bot first, then add your chat_id to "
                "config.user.yaml under channels.telegram.notify_chat_id"
            )

        # Convert markdown to Telegram HTML
        from semeclaw.channel.telegram_channel import _md_to_html
        html = _md_to_html(args["message"])

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": notify_chat_id,
            "text": html,
            "parse_mode": "HTML",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    return "✓ Telegram notification sent"
                else:
                    # Fallback: try plain text
                    payload["text"] = args["message"]
                    del payload["parse_mode"]
                    resp2 = await client.post(url, json=payload)
                    if resp2.status_code == 200:
                        return "✓ Telegram notification sent (plain text)"
                    return f"Error sending notification: {resp.text}"
        except Exception as e:
            return f"Error: {e}"


def create_experiment_tools(workspace_path: Path) -> list[BaseTool]:
    """Create all experiment and notification tools."""
    return [
        LogExperimentTool(workspace_path),
        GetExperimentSummaryTool(workspace_path),
        TelegramNotifyTool(workspace_path),
    ]
