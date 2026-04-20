"""Shared dependencies for dashboard routers."""
import json
import logging
import os
from pathlib import Path
from typing import Optional

try:
    from fastapi import Request
except ImportError:  # pragma: no cover
    Request = None  # type: ignore

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------
SEMECLAW_API_KEY = os.environ.get("SEMECLAW_API_KEY", "").strip()
SEMECLAW_CORS_ORIGINS = os.environ.get("SEMECLAW_CORS_ORIGINS", "*")
SEMECLAW_FRAME_ANCESTORS = os.environ.get("SEMECLAW_FRAME_ANCESTORS", "*")
SEMECLAW_TENANT_ID = os.environ.get("SEMECLAW_TENANT_ID", "default")
SEMECLAW_PUBLIC_URL = os.environ.get("SEMECLAW_PUBLIC_URL", "http://127.0.0.1:8765").rstrip("/")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
WAR_ROOM_DIR = Path(__file__).parent.parent
STATE_FILE   = WAR_ROOM_DIR / "shared_state.json"
LOGS_DIR     = WAR_ROOM_DIR / "logs"
RESEARCH_DIR = WAR_ROOM_DIR / "research"
AGENTS_DIR   = WAR_ROOM_DIR / "agents"
CONFIG_FILE  = WAR_ROOM_DIR / "config.json"

ROOT = WAR_ROOM_DIR.parent

logger = logging.getLogger("war_room.dashboard")

# ---------------------------------------------------------------------------
# Stripe billing scaffold
# ---------------------------------------------------------------------------
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "").strip()
STRIPE_PUBLISHABLE_KEY   = os.environ.get("STRIPE_PUBLISHABLE_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
STRIPE_PRICE_PER_MEETING = os.environ.get("STRIPE_PRICE_PER_MEETING", "").strip()

# ---------------------------------------------------------------------------
# App version
# ---------------------------------------------------------------------------
APP_VERSION = "0.7.0"

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------
_METRICS = {
    "meetings_started":   0,
    "meetings_finalized": 0,
    "questions_asked":    0,
    "tts_requests":       0,
    "reports_created":    0,
    "webhooks_fired":     0,
}


def _bump(key: str, n: int = 1) -> None:
    _METRICS[key] = _METRICS.get(key, 0) + n


# ---------------------------------------------------------------------------
# Cost ledger
# ---------------------------------------------------------------------------
_COST_LEDGER: dict[str, dict[str, int]] = {}  # tenant_id -> {metric: count}


def _cost_bump(tenant: str, metric: str, n: int = 1) -> None:
    bucket = _COST_LEDGER.setdefault(tenant, {})
    bucket[metric] = bucket.get(metric, 0) + n


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------
def _tenant_id(request) -> str:
    """Resolve tenant id. Header wins, then env default."""
    if Request is not None and hasattr(request, "headers"):
        t = (request.headers.get("x-tenant-id") or "").strip()
    else:
        t = ""
    return t or SEMECLAW_TENANT_ID or "default"
