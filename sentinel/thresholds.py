"""
sentinel/thresholds.py — configurable health thresholds for Sentinel.
All values can be overridden via env vars prefixed SENTINEL_.
"""
import json
import os


def _int(key: str, default: int) -> int:
    return int(os.environ.get(f"SENTINEL_{key}", default))

def _float(key: str, default: float) -> float:
    return float(os.environ.get(f"SENTINEL_{key}", default))


def _json(key: str, default):
    raw = os.environ.get(f"SENTINEL_{key}", "").strip()
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


# ── Probe intervals ──────────────────────────────────────────────────────────
PROBE_INTERVAL_SEC   = _int("PROBE_INTERVAL_SEC", 60)       # full probe cycle
PROBE_TIMEOUT_SEC    = _float("PROBE_TIMEOUT_SEC", 8.0)     # per-host timeout
ALERT_DEDUP_SEC      = _int("ALERT_DEDUP_SEC", 600)         # 10-min de-dup window

# ── Resource limits ───────────────────────────────────────────────────────────
RAM_FREE_MB_MIN      = _int("RAM_FREE_MB_MIN", 500)
DISK_USED_PCT_MAX    = _int("DISK_USED_PCT_MAX", 85)
HEARTBEAT_AGE_SEC_MAX= _int("HEARTBEAT_AGE_SEC_MAX", 300)   # 5 min
GATEWAY_LATENCY_MS_MAX = _int("GATEWAY_LATENCY_MS_MAX", 2000)
AGENT_HEALTH_PCT_MIN = _int("AGENT_HEALTH_PCT_MIN", 70)     # warn below 70%
AGENT_HEALTH_PCT_CRIT= _int("AGENT_HEALTH_PCT_CRIT", 50)    # critical below 50%

# ── Tailscale IPs and ports ───────────────────────────────────────────────────
_DEFAULT_DROPLET_PROBES = [
    {"agent": "Dexter", "host": "100.94.135.19",  "port": 2222,
     "gateway": "http://100.94.135.19:18789/health"},
    {"agent": "Memo",   "host": "100.88.192.48",  "port": 22,
     "gateway": "http://100.88.192.48:18789/health"},
    {"agent": "Sienna", "host": "100.124.88.93",  "port": 22,
     "gateway": "http://100.124.88.93:18789/health"},
    {"agent": "Nano",   "host": "100.105.148.29", "port": 22,
     "gateway": "http://100.105.148.29:18789/health"},
]
DROPLET_PROBES = _json("DROPLET_PROBES_JSON", _DEFAULT_DROPLET_PROBES)
SSH_KEY_PATH = os.environ.get(
    "SENTINEL_SSH_KEY_PATH",
    os.path.expanduser("~/.ssh/id_ed25519_agent"),
)
AGENT_USERS = _json("AGENT_USERS_JSON", {
    "100.94.135.19": "Dexter1981",
    "100.88.192.48": "Memo1981",
    "100.124.88.93": "Sienna1981",
    "100.105.148.29": "Nano1981",
})

# ── Tripwire ─────────────────────────────────────────────────────────────────
TRIPWIRE_INTERVAL_SEC = _int("TRIPWIRE_INTERVAL_SEC", 300)  # 5 min
TRIPWIRE_BASELINE_PATH = os.path.expanduser("~/.openclaw/tripwire/baseline.json")
TRIPWIRE_VAULT_DIR     = os.path.expanduser("~/.openclaw/tripwire/vault")
TRIPWIRE_VAULT_DAYS    = _int("TRIPWIRE_VAULT_DAYS", 90)

_DEFAULT_WATCHED_PATHS_MAC = [
    "~/.openclaw/openclaw.json",
    "~/.openclaw/fleet.env",
    "~/.openclaw/cron/jobs.json",
    os.path.expanduser("~/SemeClaw/war_room/dashboard/server.py"),
    os.path.expanduser("~/SemeClaw/war_room/dashboard/index.html"),
    os.path.expanduser("~/.ssh/config"),
]
WATCHED_PATHS_MAC = _json("WATCHED_PATHS_JSON", _DEFAULT_WATCHED_PATHS_MAC)
