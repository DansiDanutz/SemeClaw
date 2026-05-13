"""
kpis/collector.py — Real-time KPI collector.

Consumes Redis stream dls.results and increments daily counters
in Supabase project_kpis_daily.

Also exposes helper functions called by digest.py.
"""
import asyncio
import json
import logging
import os
from datetime import date
from pathlib import Path

logger = logging.getLogger("kpis.collector")

# ── Supabase client ───────────────────────────────────────────────────────────
_sb = None


def _get_supabase():
    global _sb
    if _sb is not None:
        return _sb
    try:
        from supabase import create_client
        fleet_env = Path.home() / ".openclaw" / "fleet.env"
        url, key = "", ""
        if fleet_env.exists():
            for line in fleet_env.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    k = k.strip(); v = v.strip().strip('"').strip("'")
                    if k == "DLS_TEAM_SUPABASE_URL":
                        url = v
                    elif k == "DLS_TEAM_SUPABASE_SERVICE_KEY":
                        key = v
        url  = url  or os.environ.get("DLS_TEAM_SUPABASE_URL", "")
        key  = key  or os.environ.get("DLS_TEAM_SUPABASE_SERVICE_KEY", "")
        if url and key:
            _sb = create_client(url, key)
            logger.info("Supabase client ready: %s", url[:50])
    except Exception as e:
        logger.error("Supabase init failed: %s", e)
    return _sb


def _today() -> str:
    return date.today().isoformat()


# ── Core upsert ───────────────────────────────────────────────────────────────

async def upsert_kpi(
    project: str,
    day: str | None = None,
    *,
    tasks_completed: int = 0,
    tasks_failed: int = 0,
    tokens_spent: int = 0,
    cost_usd: float = 0.0,
    new_commits: int = 0,
    deploys: int = 0,
    blockers: list | None = None,
    evolved: bool | None = None,
    owner_agent: str | None = None,
) -> bool:
    """Upsert daily KPI row for a project. Increments numeric counters."""
    sb  = _get_supabase()
    if not sb:
        logger.warning("Supabase not available — KPI not recorded: %s", project)
        return False

    day = day or _today()
    try:
        # Fetch existing row
        r = sb.table("project_kpis_daily").select("*").eq("day", day).eq("project", project).execute()
        existing = r.data[0] if r.data else {}

        row = {
            "day":              day,
            "project":          project,
            "tasks_completed":  (existing.get("tasks_completed") or 0) + tasks_completed,
            "tasks_failed":     (existing.get("tasks_failed") or 0) + tasks_failed,
            "tokens_spent":     (existing.get("tokens_spent") or 0) + tokens_spent,
            "cost_usd":         float(existing.get("cost_usd") or 0) + cost_usd,
            "new_commits":      (existing.get("new_commits") or 0) + new_commits,
            "deploys":          (existing.get("deploys") or 0) + deploys,
        }
        if blockers is not None:
            existing_blockers = existing.get("blockers") or []
            row["blockers"] = list(set(existing_blockers + blockers))
        if evolved is not None:
            row["evolved"] = evolved
        if owner_agent:
            row["owner_agent"] = owner_agent

        sb.table("project_kpis_daily").upsert(row).execute()
        logger.debug("KPI upserted: %s/%s +%d done +%d failed", day, project, tasks_completed, tasks_failed)
        return True
    except Exception as e:
        logger.error("KPI upsert error: %s", e)
        return False


# ── Redis stream consumer ─────────────────────────────────────────────────────

async def consume_results_stream(last_id: str = "$") -> str:
    """
    Read from dls.results stream and increment KPI counters.
    Returns the last consumed stream ID.
    """
    try:
        import redis.asyncio as aioredis
        r = aioredis.Redis(host="localhost", port=6379, decode_responses=True)
        results = await r.xread({"dls.results": last_id}, count=100, block=1000)
        await r.aclose()
        if not results:
            return last_id

        for _, messages in results:
            for entry_id, fields in messages:
                last_id = entry_id
                try:
                    data = json.loads(fields.get("data", "{}"))
                    project  = data.get("project") or "unknown"
                    status   = data.get("status", "")
                    tokens   = int(data.get("tokens_used", 0))
                    cost     = float(data.get("cost_usd", 0))
                    agent    = data.get("agent", "")

                    await upsert_kpi(
                        project,
                        tasks_completed=1 if status in ("success", "done", "completed") else 0,
                        tasks_failed=1 if status in ("failed", "error", "timeout") else 0,
                        tokens_spent=tokens,
                        cost_usd=cost,
                        owner_agent=agent,
                    )
                except Exception as e:
                    logger.error("Result parse error: %s", e)
    except Exception as e:
        logger.warning("Results stream error: %s", e)

    return last_id


async def collector_loop():
    """Background loop: consume dls.results forever."""
    logger.info("KPI collector started — watching dls.results")
    last_id = "$"
    while True:
        try:
            last_id = await consume_results_stream(last_id)
        except Exception as e:
            logger.error("Collector loop error: %s", e)
            await asyncio.sleep(5)


# ── Query helpers ─────────────────────────────────────────────────────────────

def get_today_kpis() -> list[dict]:
    """Fetch today's KPI rows for all projects."""
    sb = _get_supabase()
    if not sb:
        return []
    try:
        r = sb.table("project_kpis_daily").select("*").eq("day", _today()).execute()
        return r.data or []
    except Exception as e:
        logger.error("KPI fetch error: %s", e)
        return []


def get_projects_state() -> list[dict]:
    """Fetch all projects from projects_state table."""
    sb = _get_supabase()
    if not sb:
        return []
    try:
        r = sb.table("projects_state").select("*").execute()
        return r.data or []
    except Exception as e:
        logger.error("Projects state fetch error: %s", e)
        return []
