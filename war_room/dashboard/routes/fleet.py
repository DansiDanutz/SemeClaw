"""Agent-fleet surface — run tracking, health, history, droplet probes.

Extracted from server.py (Phase 3.1 of the improvement goal, slice 4). Owns:

    POST /api/agent/run-start            — record an agent run (status=running)
    POST /api/agent/run-complete         — mark success/failed, broadcast health
    POST /api/agent/health/probe         — trigger an immediate droplet probe
    GET  /api/agent/health               — all-agent health (Supabase + Paperclip)
    GET  /api/agent/history/{agent_name} — last 100 runs for one agent

plus the droplet connectivity probe machinery and its background loop
(`droplet_probe_loop`), which server.py starts at app startup.

Shared infrastructure (`_supa` Supabase helper, `_prune_agent_history`,
`_get_company_id`, `PAPERCLIP_BASE`) still lives in server.py and is
resolved lazily at call time — server.py imports this module at startup,
so module-level imports back into it would be circular.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from war_room.dashboard.websocket_manager import manager

logger = logging.getLogger("war_room.dashboard.fleet")
router = APIRouter(tags=["fleet"])


def _srv():
    """The server module, resolved lazily (see module docstring)."""
    from war_room.dashboard import server

    return server


@router.post("/api/agent/run-start")
async def api_agent_run_start(request: Request):
    """Record that an agent was assigned a task (status=running).
    Returns the run_id to use when calling /run-complete."""
    srv = _srv()
    data = await request.json()
    agent = data.get("agent_name", "").strip()
    source = data.get("agent_source", "unknown")
    task_ref = data.get("task_ref", "")
    if not agent:
        return JSONResponse({"error": "agent_name required"}, status_code=400)

    run_id = str(uuid.uuid4())
    try:
        rows = await srv._supa(
            "post",
            "agent_run_history",
            json={
                "agent_name": agent,
                "agent_source": source,
                "status": "running",
                "task_ref": task_ref or None,
                "run_id": run_id,
            },
        )
        row = rows[0] if rows else {"run_id": run_id}  # noqa: F841 - parity with original
    except Exception as e:
        logger.error("run-start insert: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)

    await manager.broadcast(
        {
            "type": "agent_run_start",
            "agent_name": agent,
            "run_id": run_id,
            "task_ref": task_ref,
        }
    )
    return JSONResponse({"run_id": run_id, "agent_name": agent})


@router.post("/api/agent/run-complete")
async def api_agent_run_complete(request: Request):
    """Mark a run as success or failed. Prunes to last 100 and broadcasts health update."""
    srv = _srv()
    data = await request.json()
    run_id = data.get("run_id", "").strip()
    agent = data.get("agent_name", "").strip()
    status = data.get("status", "success")  # success | failed
    reason = data.get("reason", "")  # failure reason

    if status not in ("success", "failed"):
        return JSONResponse({"error": "status must be success or failed"}, status_code=400)
    if not agent:
        return JSONResponse({"error": "agent_name required"}, status_code=400)

    try:
        patch = {"status": status}
        if reason:
            patch["reason"] = reason
        if run_id:
            await srv._supa("patch", f"agent_run_history?run_id=eq.{run_id}", json=patch)
        else:
            # Fallback: update the most recent running row for this agent
            await srv._supa(
                "patch",
                f"agent_run_history?agent_name=eq.{agent}&status=eq.running&order=created_at.desc&limit=1",
                json=patch,
            )
        # Prune to 100
        await srv._prune_agent_history(agent)
    except Exception as e:
        logger.error("run-complete patch: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)

    # Fetch updated health for this agent and broadcast
    health = await _get_agent_health_single(agent)
    await manager.broadcast(
        {
            "type": "agent_run_complete",
            "agent_name": agent,
            "run_id": run_id,
            "status": status,
            "reason": reason,
            "health": health,
        }
    )
    return JSONResponse({"ok": True, "health": health})


async def _get_agent_health_single(agent_name: str) -> dict:
    """Return health summary dict for one agent."""
    try:
        rows = await _srv()._supa(
            "get",
            f"agent_health_summary?agent_name=eq.{agent_name}&select=*",
        )
        return rows[0] if rows else {}
    except Exception:
        return {}


# ── Droplet connectivity probe ────────────────────────────────────────────────
# Uses Tailscale IPs (from ~/.ssh/config) — reliable mesh, not public IPs.
# Dexter uses port 2222 (as per SSH config); others use 22.
# Also probes OpenClaw gateway :18789 as the primary health signal.
_DROPLET_PROBES: list[dict] = [
    {"agent": "Dexter", "host": "100.94.135.19", "port": 2222, "gateway": "http://100.94.135.19:18789/health"},
    {"agent": "Memo", "host": "100.88.192.48", "port": 22, "gateway": "http://100.88.192.48:18789/health"},
    {"agent": "Sienna", "host": "100.124.88.93", "port": 22, "gateway": "http://100.124.88.93:18789/health"},
    {"agent": "Nano", "host": "100.105.148.29", "port": 22, "gateway": "http://100.105.148.29:18789/health"},
]
_PROBE_INTERVAL = 300  # 5 minutes
_PROBE_TIMEOUT = 8.0  # seconds per probe


async def _probe_one(agent: str, host: str, port: int, gateway: str | None = None) -> tuple[str, str | None]:
    """Probe a droplet. Primary: HTTP GET gateway /health. Fallback: TCP connect SSH port."""
    # 1. Gateway HTTP probe (preferred — confirms OpenClaw is alive, not just SSH)
    if gateway:
        try:
            async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as c:
                r = await c.get(gateway)
                if r.status_code == 200:
                    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
                    if data.get("status") in ("live", "ok") or r.status_code == 200:
                        return "success", None
                return "failed", f"gateway HTTP {r.status_code}"
        except Exception:
            pass  # fall through to TCP probe

    # 2. TCP SSH port fallback
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=_PROBE_TIMEOUT)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return "success", "ssh-only (gateway unreachable)"
    except asyncio.TimeoutError:
        return "failed", f"TCP timeout {host}:{port} after {_PROBE_TIMEOUT}s"
    except OSError as e:
        return "failed", f"TCP {host}:{port}: {e}"
    except Exception as e:
        return "failed", str(e)


async def _record_probe(agent: str, status: str, reason: str | None) -> None:
    """Write probe result to agent_run_history in Supabase."""
    try:
        await _srv()._supa(
            "post",
            "agent_run_history",
            json={
                "agent_name": agent,
                "status": status,
                "reason": reason,
                "task_ref": "connectivity-probe",
            },
        )
    except Exception as e:
        logger.warning(f"probe record failed for {agent}: {e}")


async def _run_all_probes() -> list[dict]:
    """Probe all droplets concurrently and record results."""
    tasks = [_probe_one(p["agent"], p["host"], p["port"], p.get("gateway")) for p in _DROPLET_PROBES]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out = []
    for probe, result in zip(_DROPLET_PROBES, results):
        if isinstance(result, Exception):
            status, reason = "failed", str(result)
        else:
            status, reason = result
        await _record_probe(probe["agent"], status, reason)
        out.append({"agent": probe["agent"], "status": status, "reason": reason})
        logger.info(f"probe {probe['agent']} ({probe['host']}:{probe['port']}) → {status}")
    return out


async def droplet_probe_loop() -> None:
    """Background loop: probe every _PROBE_INTERVAL seconds. Started by server.py."""
    await asyncio.sleep(10)  # brief startup delay
    while True:
        try:
            await _run_all_probes()
        except Exception as e:
            logger.warning(f"probe loop error: {e}")
        await asyncio.sleep(_PROBE_INTERVAL)


@router.post("/api/agent/health/probe")
async def api_probe_now(request: Request):
    """Manually trigger an immediate connectivity probe of all droplets."""
    results = await _run_all_probes()
    return JSONResponse({"ok": True, "results": results})


@router.get("/api/agent/health")
async def api_agent_health():
    """Return health summary for ALL agents — merges Supabase tracked agents with
    Paperclip heartbeat-runs so every agent card shows real dot history."""
    srv = _srv()

    # ── 1. Supabase tracked agents (war_room dispatch system) ──────────────────
    supa_result: list[dict] = []
    try:
        summary = await srv._supa("get", "agent_health_summary?select=*&order=health_pct.asc")
        for row in summary:
            name = row["agent_name"]
            try:
                dots = await srv._supa(
                    "get",
                    f"agent_run_history?agent_name=eq.{name}"
                    "&status=in.(success,failed,running)"
                    "&order=created_at.desc&limit=20&select=status,reason,created_at",
                )
                row["dots"] = list(reversed(dots))
            except Exception:
                row["dots"] = []
            row["agent_source"] = "supabase"
            supa_result.append(row)
    except Exception as e:
        logger.warning("api_agent_health Supabase: %s", e)

    supa_names = {r["agent_name"] for r in supa_result}

    # ── 2. Paperclip heartbeat-runs for all other agents ──────────────────────
    pc_result: list[dict] = []
    try:
        company_id = await srv._get_company_id()
        if company_id:
            async with httpx.AsyncClient(base_url=srv.PAPERCLIP_BASE, timeout=10.0) as c:
                # Get id→name map
                ar = await c.get(f"/api/companies/{company_id}/agents")
                ar.raise_for_status()
                id_to_name: dict[str, str] = {
                    a["id"]: a["name"] for a in (ar.json() if isinstance(ar.json(), list) else [])
                }
                # Get last 200 heartbeat runs
                hr = await c.get(
                    f"/api/companies/{company_id}/heartbeat-runs",
                    params={"limit": 200},
                )
                hr.raise_for_status()
                runs = hr.json() if isinstance(hr.json(), list) else []

            by_agent: dict[str, list] = defaultdict(list)
            for r in runs:
                by_agent[r["agentId"]].append(r)

            for agent_id, agent_runs in by_agent.items():
                agent_name = id_to_name.get(agent_id, agent_id[:8])
                # Sort oldest→newest
                agent_runs.sort(key=lambda x: x.get("startedAt") or x.get("createdAt") or "")
                dots = []
                for r in agent_runs[-20:]:
                    pc_status = r.get("status", "")
                    dot_status = (
                        "success" if pc_status == "succeeded" else "failed" if pc_status == "failed" else "running"
                    )
                    dots.append(
                        {
                            "status": dot_status,
                            "reason": r.get("error") or None,
                            "created_at": r.get("startedAt") or r.get("createdAt"),
                        }
                    )
                total = len(agent_runs)
                succ = sum(1 for r in agent_runs if r.get("status") == "succeeded")
                fail = total - succ
                health = round(succ / total * 100, 1) if total else None
                pc_result.append(
                    {
                        "agent_name": agent_name,
                        "agent_source": "paperclip",
                        "total_runs": total,
                        "successes": succ,
                        "failures": fail,
                        "health_pct": health,
                        "last_run_at": agent_runs[-1].get("finishedAt") if agent_runs else None,
                        "dots": dots,
                    }
                )
    except Exception as e:
        logger.warning("api_agent_health Paperclip: %s", e)

    # ── 3. Deduplicate: merge multiple Supabase rows for the same agent ──────
    # agent_health_summary may return one row per task_ref group; combine them.
    merged_supa: dict[str, dict] = {}
    for row in supa_result:
        name = row["agent_name"]
        if name not in merged_supa:
            merged_supa[name] = dict(row)
            merged_supa[name].setdefault("dots", [])
        else:
            # Accumulate totals and merge dot lists
            merged_supa[name]["total_runs"] = (merged_supa[name].get("total_runs") or 0) + (row.get("total_runs") or 0)
            merged_supa[name]["successes"] = (merged_supa[name].get("successes") or 0) + (row.get("successes") or 0)
            merged_supa[name]["failures"] = (merged_supa[name].get("failures") or 0) + (row.get("failures") or 0)
            merged_supa[name]["dots"] = sorted(
                merged_supa[name]["dots"] + row.get("dots", []), key=lambda d: d.get("created_at") or ""
            )

    # Recompute health_pct after merge
    for name, row in merged_supa.items():
        t = row.get("total_runs") or 0
        s = row.get("successes") or 0
        row["health_pct"] = round(s / t * 100, 1) if t else None

    supa_deduped = list(merged_supa.values())
    supa_names = {r["agent_name"] for r in supa_deduped}

    # ── 4. Merge and compute system health ────────────────────────────────────
    # Use the freshest data source per agent (Supabase vs Paperclip)
    pc_by_name: dict[str, dict] = {r["agent_name"]: r for r in pc_result}
    result: list[dict] = []
    for r in supa_deduped:
        name = r["agent_name"]
        pc = pc_by_name.get(name)
        if pc:
            supa_last = r.get("last_run_at") or ""
            pc_last = pc.get("last_run_at") or ""
            if pc_last > supa_last:
                result.append(pc)
                continue
        result.append(r)
    # Add Paperclip-only agents
    for r in pc_result:
        if r["agent_name"] not in supa_names:
            result.append(r)
    valid = [r for r in result if r.get("health_pct") is not None]
    system_health = round(sum(float(r["health_pct"]) for r in valid) / len(valid), 1) if valid else None
    return JSONResponse({"agents": result, "system_health": system_health})


@router.get("/api/agent/history/{agent_name}")
async def api_agent_history(agent_name: str):
    """Last 100 run entries for one agent.
    Primary: Supabase agent_run_history (war_room dispatched agents).
    Fallback: Paperclip heartbeat-runs (all other agents).
    """
    srv = _srv()
    # 1. Try Supabase first
    try:
        rows = await srv._supa(
            "get",
            f"agent_run_history?agent_name=eq.{agent_name}"
            "&order=created_at.desc&limit=100"
            "&select=id,status,reason,task_ref,created_at",
        )
        if rows:
            return JSONResponse(rows)
    except Exception as e:
        logger.warning("api_agent_history Supabase: %s", e)

    # 2. Fallback: Paperclip heartbeat-runs
    try:
        company_id = await srv._get_company_id()
        if not company_id:
            return JSONResponse([])
        async with httpx.AsyncClient(base_url=srv.PAPERCLIP_BASE, timeout=8.0) as c:
            # Build name→id map
            ar = await c.get(f"/api/companies/{company_id}/agents")
            ar.raise_for_status()
            agents_list = ar.json() if isinstance(ar.json(), list) else []
            agent = next((a for a in agents_list if a.get("name", "").lower() == agent_name.lower()), None)
            if not agent:
                return JSONResponse([])
            agent_id = agent["id"]
            hr = await c.get(
                f"/api/companies/{company_id}/heartbeat-runs",
                params={"agentId": agent_id, "limit": 100},
            )
            hr.raise_for_status()
            runs = hr.json() if isinstance(hr.json(), list) else []
        # Convert to the same schema as agent_run_history
        converted = []
        for r in sorted(runs, key=lambda x: x.get("startedAt") or "", reverse=True):
            pc_status = r.get("status", "")
            converted.append(
                {
                    "id": r.get("id"),
                    "status": "success"
                    if pc_status == "succeeded"
                    else "failed"
                    if pc_status == "failed"
                    else pc_status,
                    "reason": r.get("error") or None,
                    "task_ref": r.get("wakeupRequestId") or None,
                    "created_at": r.get("startedAt") or r.get("createdAt"),
                    "source": r.get("invocationSource", "timer"),
                }
            )
        return JSONResponse(converted)
    except Exception as e:
        logger.error("api_agent_history Paperclip: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)
