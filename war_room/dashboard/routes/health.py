"""Health, TTS, and board routes."""

import json

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from war_room.dashboard.routes.deps import CONFIG_FILE, STATE_FILE

router = APIRouter(tags=["health"])


try:
    import httpx

    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False


@router.get("/api/board")
async def api_board():
    """Return Paperclip board state via the bridge."""
    paperclip_url = "http://127.0.0.1:3100"
    use_mock = True
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text())
            paperclip_url = cfg.get("paperclip_url", paperclip_url)
            use_mock = cfg.get("mock", True)
        except Exception:
            pass

    # Try live API
    if not use_mock and _HAS_HTTPX:
        try:
            async with httpx.AsyncClient(base_url=paperclip_url, timeout=5.0) as client:
                r = await client.get("/health")
                r.raise_for_status()
                # Try to get board
                try:
                    r = await client.get("/board")
                    r.raise_for_status()
                    return JSONResponse(r.json())
                except Exception:
                    pass
        except Exception:
            pass

    # Fallback: return from state file or mock
    state = {}
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text())
    return JSONResponse(
        state.get(
            "paperclip",
            {
                "connected": False,
                "mode": "mock",
                "agents": {
                    "Dexter": {"role": "Research", "status": "idle"},
                    "David": {"role": "Architect", "status": "idle"},
                    "Memo": {"role": "Strategist", "status": "idle"},
                    "Hermes": {"role": "Writer", "status": "idle"},
                    "Sienna": {"role": "Crypto", "status": "idle"},
                    "Nano": {"role": "AgentCreator", "status": "idle"},
                },
            },
        )
    )
