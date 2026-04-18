"""
coordinator/coordinator.py — Model Coordinator v2
Port :8996 (standby) | Primary is the existing balancer on :8997

This coordinator wraps the full fallback chain with circuit breakers.
All agents route through here. If the primary balancer fails, the chain
tries proxy2, proxy1, local Ollama, OpenRouter, Gemini, ZhiPu, then safe-mode.

API:
  POST /v1/chat/completions  — OpenAI-compatible proxy endpoint
  GET  /health               — service health
  GET  /chain                — circuit breaker state for all backends
  POST /chain/reset/{name}   — manually reset a circuit breaker
  GET  /safe_mode            — safe mode status + queue depth
"""
import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from coordinator.chain import call_with_fallback, chain_status, get_backends, CBState
from coordinator.safe_mode import is_active, queue_depth
from sentinel.alerts import send_alert

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [coordinator] %(levelname)s %(message)s",
)
logger = logging.getLogger("coordinator")

# ── Background probe loop ────────────────────────────────────────────────────

async def _probe_loop():
    """Health-probe all backends every 30s to detect recovery early.

    For each OPEN backend that has been open for at least 90s, issue a
    GET {url}/health with a 5s timeout.  Two consecutive probe failures
    keep it OPEN; a successful probe lets the normal HALF_OPEN logic take
    over on the next real request.
    """
    import httpx
    _probe_failures: dict[str, int] = {}   # backend name → consecutive probe failures

    while True:
        await asyncio.sleep(30)
        for backend in get_backends():
            if backend.state != CBState.OPEN:
                _probe_failures.pop(backend.name, None)
                continue
            now = time.time()
            if now - backend.opened_at < 90:
                continue  # too soon to probe
            health_url = f"{backend.url.rstrip('/')}/health"
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    r = await client.get(health_url)
                    r.raise_for_status()
                _probe_failures.pop(backend.name, None)
                logger.info("[probe] %s /health OK — ready for HALF_OPEN on next request", backend.name)
            except Exception as exc:
                consecutive = _probe_failures.get(backend.name, 0) + 1
                _probe_failures[backend.name] = consecutive
                logger.debug("[probe] %s /health failed (%d/2): %s", backend.name, consecutive, exc)
                if consecutive >= 2:
                    logger.warning("[probe] %s marked suspect after 2 probe failures", backend.name)


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=== Model Coordinator v2 starting on :8996 ===")
    await send_alert(
        "Model Coordinator v2 started — 9-level fallback chain active",
        level="✅",
        dedup_key="coordinator-start",
        dedup_sec=300,
    )
    asyncio.create_task(_probe_loop())
    yield
    logger.info("Coordinator shutting down")


app = FastAPI(title="DansLab Model Coordinator v2", version="2.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8765",
        "http://127.0.0.1:8765",
        "http://localhost:18790",
        "http://127.0.0.1:18790",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    backends = get_backends()
    healthy  = [b for b in backends if b.state == CBState.CLOSED]
    return {
        "status":       "ok" if healthy else "degraded",
        "service":      "model-coordinator",
        "version":      "2.0.0",
        "safe_mode":    is_active(),
        "queue_depth":  queue_depth(),
        "backends_ok":  len(healthy),
        "backends_total": len(backends),
    }


@app.get("/chain")
async def chain():
    return {"backends": chain_status(), "safe_mode": is_active(), "queue_depth": queue_depth()}


@app.post("/chain/reset/{backend_name}")
async def reset_backend(backend_name: str):
    """Manually reset a circuit breaker to CLOSED."""
    for b in get_backends():
        if b.name == backend_name:
            b.reset()
            logger.info("Manual reset: %s → CLOSED", backend_name)
            return {"reset": backend_name, "state": "CLOSED"}
    return JSONResponse({"error": f"Backend '{backend_name}' not found"}, status_code=404)


@app.get("/safe_mode")
async def safe_mode_status():
    return {"active": is_active(), "queue_depth": queue_depth()}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """
    OpenAI-compatible proxy. Routes through circuit-breaker chain.
    Never returns 5xx — degrades to safe-mode stub on total failure.
    """
    t0 = time.monotonic()
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    req_id = str(uuid.uuid4())[:8]
    logger.info("[%s] → chat/completions (model=%s)", req_id, payload.get("model", "?"))

    response, backend_used = await call_with_fallback(payload)

    latency = (time.monotonic() - t0) * 1000
    logger.info("[%s] ← %s in %.0fms", req_id, backend_used, latency)

    # Add routing metadata to response
    if isinstance(response, dict):
        response.setdefault("_meta", {})
        response["_meta"]["backend"]    = backend_used
        response["_meta"]["latency_ms"] = round(latency, 1)
        response["_meta"]["req_id"]     = req_id

    return response


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "coordinator.coordinator:app",
        host="0.0.0.0",
        port=8996,
        log_level="info",
        reload=False,
    )
