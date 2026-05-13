"""
sentinel/probes.py — async probe functions for each host.
Returns a ProbeResult dataclass with structured health data.
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx

from sentinel.thresholds import AGENT_USERS, DROPLET_PROBES, PROBE_TIMEOUT_SEC, SSH_KEY_PATH

logger = logging.getLogger("sentinel.probes")


@dataclass
class ProbeResult:
    agent: str
    ts: float = field(default_factory=time.time)
    reachable: bool = False
    latency_ms: float | None = None
    gateway_ok: bool = False
    ssh_ok: bool = False
    ram_free_mb: float | None = None
    disk_used_pct: float | None = None
    heartbeat_age_sec: float | None = None
    cron_count: int | None = None
    gateway_version: str | None = None
    error: str | None = None
    # Mac Studio extra
    balancer_ok: bool | None = None
    ollama_ok: bool | None = None


async def probe_gateway(host: str, gateway_url: str, timeout: float = PROBE_TIMEOUT_SEC) -> tuple[bool, float | None, str | None]:
    """Try gateway HTTP /health. Returns (ok, latency_ms, version)."""
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(gateway_url)
            latency = (time.monotonic() - t0) * 1000
            if r.status_code == 200:
                data = r.json() if r.headers.get("content-type","").startswith("application/json") else {}
                version = data.get("version") or data.get("v")
                return True, latency, version
            return False, latency, None
    except Exception as e:
        return False, None, None


async def probe_tcp(host: str, port: int, timeout: float = PROBE_TIMEOUT_SEC) -> tuple[bool, float | None]:
    """TCP connect probe. Returns (ok, latency_ms)."""
    t0 = time.monotonic()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
        latency = (time.monotonic() - t0) * 1000
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True, latency
    except (asyncio.TimeoutError, OSError) as e:
        return False, None


async def probe_ssh_stats(host: str, port: int, user: str = None) -> dict:
    """
    SSH into host, run a quick stats command.
    Returns dict with ram_free_mb, disk_used_pct, cron_count or empty on failure.
    """
    ssh_user = user or AGENT_USERS.get(host, "root")
    cmd = [
        "ssh", "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=8",
        "-o", "BatchMode=yes",
        "-i", SSH_KEY_PATH,
        "-p", str(port),
        f"{ssh_user}@{host}",
        # Output: ram_free_kb disk_use_pct cron_count
        "bash -c 'echo RAM=$(awk \"/MemAvailable/{print \\$2}\" /proc/meminfo); "
        "echo DISK=$(df / | tail -1 | awk \"{print \\$5}\" | tr -d %); "
        "echo CRON=$(crontab -l 2>/dev/null | grep -cv \"^#\")'"
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        out = stdout.decode()
        result = {}
        for line in out.splitlines():
            if line.startswith("RAM="):
                kb = float(line.split("=")[1].strip() or "0")
                result["ram_free_mb"] = kb / 1024
            elif line.startswith("DISK="):
                result["disk_used_pct"] = float(line.split("=")[1].strip() or "0")
            elif line.startswith("CRON="):
                result["cron_count"] = int(line.split("=")[1].strip() or "0")
        return result
    except Exception as e:
        logger.debug("SSH stats failed for %s: %s", host, e)
        return {}


async def probe_local_mac() -> ProbeResult:
    """Probe Mac Studio itself (localhost services)."""
    import subprocess

    result = ProbeResult(agent="David (Mac Studio)", reachable=True)
    try:
        # RAM via vm_stat on macOS — count free + speculative + purgeable
        # macOS manages memory aggressively; "available" ≈ free + speculative + purgeable
        out = subprocess.check_output(["vm_stat"], text=True, timeout=5)
        counts = {"free": 0, "speculative": 0, "purgeable": 0}
        page_size = 16384  # Apple Silicon default
        for line in out.splitlines():
            low = line.lower()
            if "page size of" in low:
                page_size = int(line.split("page size of")[1].split()[0])
            elif "pages free" in low:
                counts["free"] = int(line.split(":")[1].strip().rstrip("."))
            elif "pages speculative" in low:
                counts["speculative"] = int(line.split(":")[1].strip().rstrip("."))
            elif "pages purgeable" in low:
                counts["purgeable"] = int(line.split(":")[1].strip().rstrip("."))
        available_pages = counts["free"] + counts["speculative"] + counts["purgeable"]
        result.ram_free_mb = (available_pages * page_size) / (1024 * 1024)
    except Exception:
        pass

    try:
        # Disk
        out = subprocess.check_output(["df", "-h", "/"], text=True, timeout=5)
        pct = out.splitlines()[-1].split()[4].rstrip("%")
        result.disk_used_pct = float(pct)
    except Exception:
        pass

    # Check balancer
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get("http://localhost:8997/health")
            result.balancer_ok = r.status_code == 200
    except Exception:
        result.balancer_ok = False

    # Check Ollama
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get("http://localhost:11434/")
            result.ollama_ok = r.status_code == 200
    except Exception:
        result.ollama_ok = False

    return result


async def probe_droplet(cfg: dict) -> ProbeResult:
    """Full probe of one droplet."""
    agent  = cfg["agent"]
    host   = cfg["host"]
    port   = cfg["port"]
    gw_url = cfg.get("gateway")

    result = ProbeResult(agent=agent)

    # 1. Gateway HTTP
    if gw_url:
        gw_ok, gw_lat, gw_ver = await probe_gateway(host, gw_url)
        result.gateway_ok    = gw_ok
        result.gateway_version = gw_ver
        if gw_ok:
            result.reachable  = True
            result.latency_ms = gw_lat

    # 2. TCP SSH fallback
    if not result.reachable:
        ssh_ok, ssh_lat = await probe_tcp(host, port)
        result.ssh_ok    = ssh_ok
        result.reachable = ssh_ok
        if ssh_ok:
            result.latency_ms = ssh_lat

    # 3. SSH stats (non-blocking, best-effort)
    if result.reachable:
        stats = await probe_ssh_stats(host, port)
        result.ram_free_mb   = stats.get("ram_free_mb")
        result.disk_used_pct = stats.get("disk_used_pct")
        result.cron_count    = stats.get("cron_count")

    return result


async def probe_all() -> list[ProbeResult]:
    """Run all droplet probes + local Mac probe concurrently."""
    tasks = [probe_droplet(cfg) for cfg in DROPLET_PROBES]
    tasks.append(probe_local_mac())
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out = []
    for r in results:
        if isinstance(r, Exception):
            logger.error("Probe exception: %s", r)
        else:
            out.append(r)
    return out
