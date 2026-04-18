"""
sentinel/sentinel.py — SemeClaw Sentinel Service
FastAPI health monitoring daemon on :18790

Probes every 60s:
  - All 4 droplets (gateway HTTP + TCP SSH via Tailscale)
  - Mac Studio local services (balancer, ollama)
  - RAM, disk, heartbeat freshness

Alerts Dan via Telegram when thresholds crossed.
Exposes /health, /status, /probes for War Room dashboard integration.
"""
import asyncio
import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Add parent to path so we can import sentinel.*
sys.path.insert(0, str(Path(__file__).parent.parent))

from sentinel.probes import probe_all, ProbeResult
from sentinel.alerts import send_alert, send_recovery
from sentinel.tripwire import tripwire_loop
from sentinel.thresholds import (
    PROBE_INTERVAL_SEC,
    RAM_FREE_MB_MIN,
    DISK_USED_PCT_MAX,
    HEARTBEAT_AGE_SEC_MAX,
    GATEWAY_LATENCY_MS_MAX,
    AGENT_HEALTH_PCT_MIN,
    AGENT_HEALTH_PCT_CRIT,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("sentinel")

# ── State ─────────────────────────────────────────────────────────────────────
_latest_probes: list[dict] = []
_last_probe_ts: float = 0.0
_alert_state: dict[str, str] = {}  # agent → "ok" | "warn" | "crit"

# ── Threshold checks ──────────────────────────────────────────────────────────

def _check_result(r: ProbeResult) -> list[str]:
    """Return list of violation strings for a probe result."""
    issues = []
    if not r.reachable:
        issues.append(f"unreachable (SSH+gateway both failed)")
    if r.ram_free_mb is not None and r.ram_free_mb < RAM_FREE_MB_MIN:
        issues.append(f"RAM low: {r.ram_free_mb:.0f}MB free (min {RAM_FREE_MB_MIN}MB)")
    if r.disk_used_pct is not None and r.disk_used_pct > DISK_USED_PCT_MAX:
        issues.append(f"Disk {r.disk_used_pct:.0f}% used (max {DISK_USED_PCT_MAX}%)")
    if r.latency_ms is not None and r.latency_ms > GATEWAY_LATENCY_MS_MAX:
        issues.append(f"Latency {r.latency_ms:.0f}ms (max {GATEWAY_LATENCY_MS_MAX}ms)")
    if r.balancer_ok is False:
        issues.append("Claude balancer (:8997) DOWN")
    if r.ollama_ok is False:
        issues.append("Ollama (:11434) DOWN")
    return issues


async def _fire_alerts(r: ProbeResult, issues: list[str]):
    """Send alerts for new issues, send recovery when issues clear."""
    agent = r.agent
    prev  = _alert_state.get(agent, "ok")
    now_state = "crit" if not r.reachable else ("warn" if issues else "ok")

    if now_state != "ok" and prev == "ok":
        # New problem
        issue_text = " | ".join(issues)
        await send_alert(
            f"*{agent}* degraded: {issue_text}",
            level="🔴" if now_state == "crit" else "⚠️",
            dedup_key=f"sentinel-{agent}-{now_state}",
        )
    elif now_state == "ok" and prev != "ok":
        # Recovery
        await send_recovery(
            f"*{agent}* recovered — all checks passing",
            dedup_key=f"sentinel-{agent}-recovery",
        )

    _alert_state[agent] = now_state


def _result_to_dict(r: ProbeResult) -> dict:
    return {
        "agent":          r.agent,
        "ts":             r.ts,
        "reachable":      r.reachable,
        "latency_ms":     round(r.latency_ms, 1) if r.latency_ms else None,
        "gateway_ok":     r.gateway_ok,
        "ssh_ok":         r.ssh_ok,
        "ram_free_mb":    round(r.ram_free_mb, 0) if r.ram_free_mb else None,
        "disk_used_pct":  r.disk_used_pct,
        "cron_count":     r.cron_count,
        "gateway_version":r.gateway_version,
        "balancer_ok":    r.balancer_ok,
        "ollama_ok":      r.ollama_ok,
        "issues":         _check_result(r),
        "status":         _alert_state.get(r.agent, "ok"),
    }


# ── Probe loop ────────────────────────────────────────────────────────────────

async def _probe_loop():
    global _latest_probes, _last_probe_ts
    logger.info("Sentinel probe loop started (interval=%ds)", PROBE_INTERVAL_SEC)

    while True:
        try:
            results = await probe_all()
            _last_probe_ts = time.time()

            probe_dicts = []
            for r in results:
                issues = _check_result(r)
                await _fire_alerts(r, issues)
                probe_dicts.append(_result_to_dict(r))

            _latest_probes = probe_dicts

            ok  = sum(1 for p in probe_dicts if not p["issues"])
            bad = len(probe_dicts) - ok
            logger.info("Probe cycle done: %d/%d healthy", ok, len(probe_dicts))
            if bad:
                logger.warning("%d agents with issues", bad)

        except Exception as e:
            logger.error("Probe loop error: %s", e)

        await asyncio.sleep(PROBE_INTERVAL_SEC)


# ── App lifecycle ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=== Sentinel starting on :18790 ===")
    await send_alert("Sentinel started — monitoring all agents", level="✅",
                     dedup_key="sentinel-start", dedup_sec=300)
    asyncio.create_task(_probe_loop())
    asyncio.create_task(tripwire_loop())
    yield
    logger.info("Sentinel shutting down")


app = FastAPI(title="SemeClaw Sentinel", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8765",
        "http://127.0.0.1:8765",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "sentinel", "version": "1.0.0"}


@app.get("/status")
async def status():
    """Summary status — for dashboard badge."""
    if not _latest_probes:
        return {"ready": False, "message": "First probe not complete yet"}

    all_ok   = all(not p["issues"] for p in _latest_probes)
    critical = [p["agent"] for p in _latest_probes if not p.get("reachable")]
    warnings = [p["agent"] for p in _latest_probes if p["issues"] and p.get("reachable")]

    return {
        "ready":       True,
        "all_ok":      all_ok,
        "critical":    critical,
        "warnings":    warnings,
        "probe_age_s": round(time.time() - _last_probe_ts, 0),
        "agents":      len(_latest_probes),
    }


@app.get("/probes")
async def probes():
    """Full probe data for all agents."""
    return {
        "ts":     _last_probe_ts,
        "probes": _latest_probes,
    }


@app.post("/probe/trigger")
async def trigger_probe():
    """Manually trigger a full probe cycle."""
    from sentinel.probes import probe_all as _probe_all
    results = await _probe_all()
    dicts   = []
    for r in results:
        issues = _check_result(r)
        await _fire_alerts(r, issues)
        dicts.append(_result_to_dict(r))
    global _latest_probes, _last_probe_ts
    _latest_probes = dicts
    _last_probe_ts = time.time()
    return {"triggered": True, "results": dicts}


@app.post("/tripwire/approve/{sha_prefix}")
async def tripwire_approve(sha_prefix: str):
    """Approve a file change (suppress alert and update baseline)."""
    from sentinel.tripwire import approve
    approve(sha_prefix)
    return {"approved": sha_prefix, "message": "Baseline will update on next check"}


@app.get("/tripwire/baseline")
async def tripwire_baseline():
    """Return current tripwire baseline."""
    from sentinel.tripwire import load_baseline
    return {"baseline": load_baseline()}


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "sentinel.sentinel:app",
        host="0.0.0.0",
        port=18790,
        log_level="info",
        reload=False,
    )
