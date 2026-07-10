"""Shared dependencies for dashboard routers."""

import json
import logging
import os
from pathlib import Path

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
# Public URL of the advertiser console (Vercel). Used for OAuth redirect + Stripe
# return URLs. Falls back to SEMECLAW_PUBLIC_URL for local dev.
ADCLAW_PUBLIC_URL = os.environ.get("ADCLAW_PUBLIC_URL", SEMECLAW_PUBLIC_URL).rstrip("/")
SEMECLAW_MANIFEST_URL = os.environ.get(
    "SEMECLAW_MANIFEST_URL", "https://semeclaw-updates.1fc06dad3c7f42576be40fc6437f8fec.workers.dev/manifest.json"
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
WAR_ROOM_DIR = Path(__file__).parent.parent.parent
STATE_FILE = WAR_ROOM_DIR / "shared_state.json"
LOGS_DIR = WAR_ROOM_DIR / "logs"
RESEARCH_DIR = WAR_ROOM_DIR / "research"
AGENTS_DIR = WAR_ROOM_DIR / "agents"
CONFIG_FILE = WAR_ROOM_DIR / "config.json"

ROOT = WAR_ROOM_DIR.parent

logger = logging.getLogger("war_room.dashboard")

# ---------------------------------------------------------------------------
# Stripe billing scaffold
# ---------------------------------------------------------------------------
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "").strip()
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
STRIPE_PRICE_PER_MEETING = os.environ.get("STRIPE_PRICE_PER_MEETING", "").strip()


# ---------------------------------------------------------------------------
# App version (read from pyproject.toml so it stays in sync with releases)
# ---------------------------------------------------------------------------
def _read_version() -> str:
    # 1. regex on local pyproject.toml — no external deps, works on all supported runtimes
    try:
        pyproject = ROOT / "pyproject.toml"
        if pyproject.exists():
            txt = pyproject.read_text(encoding="utf-8")
            import re as _re

            m = _re.search(r'^version\s*=\s*"([^"]+)"', txt, _re.MULTILINE)
            if m:
                return m.group(1)
    except Exception:
        pass
    # 2. importlib.metadata — for installed packages
    try:
        from importlib.metadata import version as _pkg_version

        return _pkg_version("semeclaw")
    except Exception:
        pass
    # 3. tomllib — Python 3.11+ only
    try:
        import tomllib

        pyproject = ROOT / "pyproject.toml"
        if pyproject.exists():
            with pyproject.open("rb") as f:
                data = tomllib.load(f)
                v = data.get("project", {}).get("version")
                if v:
                    return v
    except Exception:
        pass
    # 4. visible sentinel so we know resolution failed
    return "0.0.0"


APP_VERSION = _read_version()

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------
_METRICS = {
    "meetings_started": 0,
    "meetings_finalized": 0,
    "questions_asked": 0,
    "tts_requests": 0,
    "reports_created": 0,
    "webhooks_fired": 0,
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


# ---------------------------------------------------------------------------
# Demo agents
# ---------------------------------------------------------------------------
_DEMO_AGENTS: list[dict] = []
try:
    if os.environ.get("DEMO_MODE"):
        import sys as _sys

        _sys.path.insert(0, str(ROOT))
        from demo.loader import DEMO_AGENTS

        _DEMO_AGENTS = DEMO_AGENTS
except Exception:
    pass

# ---------------------------------------------------------------------------
# Auth / retention
# ---------------------------------------------------------------------------
_PROTECTED_WRITE_PATHS = (
    "/api/reports",
    "/api/reports/upload",
    "/api/reports/delete",
    "/api/meeting/pin",
    "/api/meeting/unpin",
    "/api/meeting/execute",
    "/api/meeting/task",
    "/api/meeting/say",
    "/api/meeting/ai-respond",
    "/api/meeting/inject",
    "/api/meeting/replan",
    "/api/meeting/finalize",
    "/api/meeting/redirect",
    "/api/webhooks",
    "/api/webhooks/delete",
    "/api/run",
    "/api/agent/run-start",
    "/api/agent/run-complete",
    "/api/voices/clone",
    "/api/voices/map",
    "/api/voice-agents",
    "/api/assistant/message",
    "/api/assistant/end",
    "/api/assistant/call",
    "/api/billing/report-usage",
    "/api/alert-dan",
)

MEETING_RETENTION_HOURS = 48
REPORT_RETENTION_HOURS = 48


# ---------------------------------------------------------------------------
# Supabase — shared credentials for all routers
# ---------------------------------------------------------------------------
def _load_supa_creds() -> tuple[str, str]:
    url = os.environ.get("DLS_TEAM_SUPABASE_URL", "").strip()
    key = os.environ.get("DLS_TEAM_SUPABASE_SERVICE_KEY", "").strip()
    if not url or not key:
        fe = Path.home() / ".openclaw" / "fleet.env"
        if fe.exists():
            for line in fe.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k.startswith("export "):
                        k = k[7:].strip()
                    if k == "DLS_TEAM_SUPABASE_URL" and not url:
                        url = v
                    elif k == "DLS_TEAM_SUPABASE_SERVICE_KEY" and not key:
                        key = v
    return url, key


SUPA_URL, SUPA_KEY = _load_supa_creds()
SUPA_HEADERS = {
    "apikey": SUPA_KEY,
    "Authorization": f"Bearer {SUPA_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

# ---------------------------------------------------------------------------
# TTS / voice maps
# ---------------------------------------------------------------------------
_ELEVEN_VOICES: dict[str, str] = {}
try:
    _ELEVEN_VOICE_JSON = Path(__file__).parent.parent / ".eleven_voices.json"
    if _ELEVEN_VOICE_JSON.exists():
        _ELEVEN_VOICES = json.loads(_ELEVEN_VOICE_JSON.read_text())
except Exception:
    pass


# ---------------------------------------------------------------------------
# Advertiser ownership check (JWT-gated)
#
# Every /api/advertiser/{advertiser_id}/... route accepts the advertiser id
# in the path. Without verification any authenticated advertiser could read
# or mutate another advertiser's data by guessing IDs. We verify that the
# caller holds a Supabase JWT whose `sub` claim matches the path parameter.
#
# Modes:
#   SEMECLAW_ADVERTISER_AUTH_STRICT = "1" (default)  -> reject on mismatch
#   SEMECLAW_ADVERTISER_AUTH_STRICT = "0"            -> audit mode: log only
#
# Demo:
#   SEMECLAW_ADVERTISER_DEMO_ID (e.g. "demo")        -> bypass for this id
#
# Requires SUPABASE_JWT_SECRET when strict; when unset the mode auto-degrades
# to "audit" with a loud warning so the service still boots.
# ---------------------------------------------------------------------------
from typing import Any as _Any

SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "").strip()
_ADVERTISER_AUTH_STRICT = os.environ.get("SEMECLAW_ADVERTISER_AUTH_STRICT", "1").strip() not in (
    "0",
    "false",
    "False",
    "no",
    "",
)
_ADVERTISER_DEMO_ID = os.environ.get("SEMECLAW_ADVERTISER_DEMO_ID", "").strip()

if _ADVERTISER_AUTH_STRICT and not SUPABASE_JWT_SECRET:
    logger.warning(
        "SEMECLAW_ADVERTISER_AUTH_STRICT=1 but SUPABASE_JWT_SECRET is unset. "
        "Falling back to audit mode until the secret is set."
    )
    _ADVERTISER_AUTH_STRICT = False


def _extract_bearer(request: _Any) -> str | None:
    """Return the bearer token from Authorization header, or None."""
    try:
        auth = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    except Exception:
        return None
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip() or None
    return None


def _decode_supabase_jwt(token: str) -> dict:
    """Decode a Supabase HS256 JWT using SUPABASE_JWT_SECRET.

    Accepts aud="authenticated" (the role Supabase stamps on logged-in users).
    """
    try:
        import jwt  # PyJWT; already pulled in transitively via supabase[auth]
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PyJWT not installed") from exc
    return jwt.decode(
        token,
        SUPABASE_JWT_SECRET,
        algorithms=["HS256"],
        audience="authenticated",
    )


def _advertiser_auth_decision(request: _Any, advertiser_id: str) -> tuple[bool, str]:
    """Decide whether a caller may act on `advertiser_id`.

    Returns (allowed, reason). In strict mode an unallowed decision should be
    surfaced as an HTTPException by the caller. In audit mode the caller
    should log the reason and proceed.
    """
    if _ADVERTISER_DEMO_ID and advertiser_id == _ADVERTISER_DEMO_ID:
        return True, "demo bypass"
    token = _extract_bearer(request)
    if not token:
        return False, "missing bearer"
    try:
        claims = _decode_supabase_jwt(token)
    except Exception as e:  # noqa: BLE001 -- any decode error = reject
        return False, f"jwt decode failed: {e}"
    sub = str(claims.get("sub", ""))
    if not sub:
        return False, "jwt has no sub"
    if sub != advertiser_id:
        return False, f"sub={sub[:8]}... != path={advertiser_id[:8]}..."
    return True, "ok"


async def require_advertiser_owner(request: _Any, advertiser_id: str) -> None:
    """Raise HTTPException if the caller does not own `advertiser_id`.

    In audit mode this never raises; it only logs. Flip strict by setting
    SEMECLAW_ADVERTISER_AUTH_STRICT=1 and providing SUPABASE_JWT_SECRET.
    """
    allowed, reason = _advertiser_auth_decision(request, advertiser_id)
    if allowed:
        return
    if _ADVERTISER_AUTH_STRICT:
        from fastapi import HTTPException

        status = 401 if reason in ("missing bearer", "jwt has no sub") or "decode" in reason else 403
        raise HTTPException(status_code=status, detail=reason)
    logger.warning("advertiser_auth audit: %s (path=%s)", reason, advertiser_id)
