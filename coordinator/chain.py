"""
coordinator/chain.py — Fallback chain with circuit breakers.

Each backend has:
  - state: CLOSED (healthy) | OPEN (broken) | HALF_OPEN (testing)
  - failure_count: consecutive failures
  - opened_at: when it tripped
  - last_probe_at: last half-open probe

Circuit breaker rules:
  - 3 consecutive failures in 60s → OPEN for 120s
  - HALF_OPEN: 1 probe every 30s — 2 successes → CLOSE
  - If ALL OPEN → safe_mode activates
"""
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum

import httpx

logger = logging.getLogger("coordinator.chain")


class CBState(str, Enum):
    CLOSED    = "CLOSED"     # healthy, passing traffic
    OPEN      = "OPEN"       # tripped, blocking traffic
    HALF_OPEN = "HALF_OPEN"  # testing recovery


# ── Fallback chain (priority order) ──────────────────────────────────────────
# Override via COORDINATOR_BACKENDS env var (JSON list of {name, url})
DEFAULT_BACKENDS = [
    {"name": "claude-balancer",     "url": "http://127.0.0.1:8997"},
    {"name": "claude-proxy2",       "url": "http://127.0.0.1:8998"},
    {"name": "claude-proxy1",       "url": "http://127.0.0.1:8999"},
    {"name": "ollama-coder",        "url": "http://127.0.0.1:11434",  "model": "qwen2.5-coder:7b", "style": "ollama"},
    {"name": "ollama-qwen3",        "url": "http://127.0.0.1:11434",  "model": "qwen3:8b",          "style": "ollama"},
    {"name": "openrouter-qwen",     "url": "https://openrouter.ai/api/v1", "model": "qwen/qwen3.6-plus:free", "style": "openai", "env_key": "OPENROUTER_API_KEY"},
    {"name": "gemini-flash",        "url": "https://generativelanguage.googleapis.com/v1beta", "model": "gemini-2.5-flash", "style": "gemini", "env_key": "GOOGLE_API_KEY"},
    {"name": "zai-glm5",            "url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-5", "style": "openai", "env_key": "ZHIPU_API_KEY"},
]

# Circuit breaker config
CB_FAILURE_THRESHOLD   = 3     # failures before OPEN
CB_FAILURE_WINDOW_SEC  = 60    # window for counting failures
CB_OPEN_DURATION_SEC   = 120   # how long to stay OPEN
CB_HALF_OPEN_PROBE_SEC = 30    # probe interval in HALF_OPEN
CB_HALF_OPEN_SUCCESSES = 2     # successes needed to CLOSE


@dataclass
class Backend:
    name:    str
    url:     str
    model:   str | None = None
    style:   str = "openai"   # openai | ollama | gemini
    env_key: str | None = None

    state:           CBState = CBState.CLOSED
    failure_count:   int     = 0
    failure_ts:      list    = field(default_factory=list)  # timestamps of recent failures
    opened_at:       float   = 0.0
    last_probe_at:   float   = 0.0
    half_open_hits:  int     = 0
    total_requests:  int     = 0
    total_successes: int     = 0
    latency_p50_ms:  float   = 0.0

    def is_available(self) -> bool:
        now = time.time()
        if self.state == CBState.CLOSED:
            return True
        if self.state == CBState.OPEN:
            if now - self.opened_at > CB_OPEN_DURATION_SEC:
                self.state = CBState.HALF_OPEN
                logger.info("[CB] %s → HALF_OPEN", self.name)
                return True  # allow one probe
            return False
        if self.state == CBState.HALF_OPEN:
            if now - self.last_probe_at > CB_HALF_OPEN_PROBE_SEC:
                return True
            return False
        return False

    def record_success(self):
        self.total_requests  += 1
        self.total_successes += 1
        if self.state == CBState.HALF_OPEN:
            self.half_open_hits += 1
            if self.half_open_hits >= CB_HALF_OPEN_SUCCESSES:
                self.state = CBState.CLOSED
                self.failure_count   = 0
                self.failure_ts      = []
                self.half_open_hits  = 0
                logger.info("[CB] %s → CLOSED (recovered)", self.name)

    def record_failure(self):
        self.total_requests += 1
        now = time.time()
        self.failure_ts = [t for t in self.failure_ts if now - t < CB_FAILURE_WINDOW_SEC]
        self.failure_ts.append(now)
        self.failure_count = len(self.failure_ts)

        if self.state == CBState.HALF_OPEN:
            # Reset half-open on any failure
            self.state        = CBState.OPEN
            self.opened_at    = now
            self.half_open_hits = 0
            logger.warning("[CB] %s → OPEN (half-open probe failed)", self.name)
        elif self.failure_count >= CB_FAILURE_THRESHOLD:
            self.state      = CBState.OPEN
            self.opened_at  = now
            logger.warning("[CB] %s → OPEN (%d failures in %ds)", self.name, self.failure_count, CB_FAILURE_WINDOW_SEC)
        else:
            self.last_probe_at = now

    def reset(self):
        """Reset circuit breaker to CLOSED state."""
        self.state           = CBState.CLOSED
        self.failure_count   = 0
        self.failure_ts      = []
        self.half_open_hits  = 0

    def to_dict(self) -> dict:
        return {
            "name":            self.name,
            "url":             self.url,
            "model":           self.model,
            "style":           self.style,
            "state":           self.state.value,
            "failure_count":   self.failure_count,
            "total_requests":  self.total_requests,
            "total_successes": self.total_successes,
            "success_rate":    round(self.total_successes / max(self.total_requests, 1) * 100, 1),
            "opened_at":       self.opened_at,
        }


# ── Chain singleton ───────────────────────────────────────────────────────────

_backends: list[Backend] = []


def _load_backends():
    import json
    import os
    raw = os.environ.get("COORDINATOR_BACKENDS")
    cfg = json.loads(raw) if raw else DEFAULT_BACKENDS
    return [Backend(**{k: v for k, v in b.items() if k in Backend.__dataclass_fields__}) for b in cfg]


def get_backends() -> list[Backend]:
    global _backends
    if not _backends:
        _backends = _load_backends()
    return _backends


def all_open() -> bool:
    return all(b.state == CBState.OPEN for b in get_backends())


def chain_status() -> list[dict]:
    return [b.to_dict() for b in get_backends()]


async def _call_openai_style(backend: Backend, payload: dict, timeout: float = 60.0) -> dict:
    """Call an OpenAI-compatible endpoint."""
    import os
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get(backend.env_key, "sk-placeholder") if backend.env_key else "sk-placeholder"
    headers["Authorization"] = f"Bearer {api_key}"

    body = dict(payload)
    if backend.model and "model" not in body:
        body["model"] = backend.model

    url = f"{backend.url.rstrip('/')}/chat/completions"
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(url, json=body, headers=headers)
        r.raise_for_status()
        return r.json()


async def _call_ollama_style(backend: Backend, payload: dict, timeout: float = 60.0) -> dict:
    """Call Ollama's /api/chat endpoint, return OpenAI-compatible response."""
    messages = payload.get("messages", [])
    body = {
        "model":  backend.model or "qwen3:8b",
        "messages": messages,
        "stream": False,
    }
    url = f"{backend.url.rstrip('/')}/api/chat"
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(url, json=body)
        r.raise_for_status()
        d = r.json()
    # Wrap in OpenAI envelope
    return {
        "id":      str(uuid.uuid4()),
        "object":  "chat.completion",
        "model":   body["model"],
        "choices": [{
            "index": 0,
            "message": d.get("message", {"role": "assistant", "content": ""}),
            "finish_reason": d.get("done_reason", "stop"),
        }],
        "usage": d.get("prompt_eval_count", 0),
    }


async def call_with_fallback(payload: dict, timeout: float = 60.0) -> tuple[dict, str]:
    """
    Try each backend in order. Returns (response, backend_name).
    Falls back to safe_mode stub if all fail.
    """
    import coordinator.safe_mode as sm
    from coordinator.safe_mode import activate, deactivate, is_active, queue_request

    backends = get_backends()
    last_error = None

    for backend in backends:
        if not backend.is_available():
            continue

        t0 = time.monotonic()
        try:
            if backend.style == "ollama":
                resp = await _call_ollama_style(backend, payload, timeout)
            else:
                resp = await _call_openai_style(backend, payload, timeout)

            latency = (time.monotonic() - t0) * 1000
            backend.record_success()
            backend.latency_p50_ms = latency
            logger.info("✅ %s responded in %.0fms", backend.name, latency)

            # If safe mode was active and we got a response, drain the queue
            if is_active():
                deactivate()
                queued = sm.drain_queue()
                if queued:
                    logger.info("Backend recovered — %d queued requests to replay", len(queued))

            return resp, backend.name

        except Exception as e:
            latency = (time.monotonic() - t0) * 1000
            backend.record_failure()
            last_error = e
            logger.warning("❌ %s failed (%.0fms): %s", backend.name, latency, e)
            continue

    # All backends exhausted — safe mode
    activate()
    req_id = str(uuid.uuid4())[:8]
    stub   = queue_request(req_id, payload)
    return stub, "safe-mode-stub"
