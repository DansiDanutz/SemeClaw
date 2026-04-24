"""
warroom/interrupts.py — Human-in-the-loop interrupt system.

Producers:  Dashboard inject UI, Telegram /inject command
Consumers:  Agent run loops (poll between steps, not mid-LLM-call)

Redis stream: dls.interrupts.{agent}
Schema:
  {
    "id":        "inj_abc123",
    "agent":     "dexter",
    "message":   "Change the auth to use JWT instead of sessions",
    "from":      "dan",
    "ts":        1713456789,
    "task_ref":  "T-1042",          # optional, links to active task
    "action":    null               # filled by agent: continue|replan|pause|handoff
  }
"""
import asyncio
import json
import logging
import os
import time
import uuid
from typing import Optional

logger = logging.getLogger("warroom.interrupts")

# ── Redis ─────────────────────────────────────────────────────────────────────
_redis = None

def _get_redis():
    global _redis
    if _redis is None:
        try:
            import redis as redis_lib
            _redis = redis_lib.Redis(host="localhost", port=6379, decode_responses=True)
            _redis.ping()
        except Exception as e:
            logger.warning("Redis not available: %s — interrupts will log-only", e)
            _redis = None
    return _redis


# ── Rate limiting ─────────────────────────────────────────────────────────────
_rate_counts: dict[str, list] = {}  # agent → list of timestamps
RATE_LIMIT = 5      # per minute
RATE_WINDOW = 60    # seconds


def _check_rate(agent: str) -> bool:
    now = time.time()
    times = [t for t in _rate_counts.get(agent, []) if now - t < RATE_WINDOW]
    if len(times) >= RATE_LIMIT:
        return False
    times.append(now)
    _rate_counts[agent] = times
    return True


# ── Active pause state ────────────────────────────────────────────────────────
_paused_agents: set[str] = set()


def is_paused(agent: str) -> bool:
    return agent.lower() in _paused_agents


def pause_agent(agent: str):
    _paused_agents.add(agent.lower())
    logger.warning("Agent PAUSED: %s", agent)


def resume_agent(agent: str):
    _paused_agents.discard(agent.lower())
    logger.info("Agent RESUMED: %s", agent)


# ── Produce an interrupt ──────────────────────────────────────────────────────

async def inject(
    agent: str,
    message: str,
    from_: str = "dan",
    task_ref: Optional[str] = None,
) -> dict:
    """
    Inject a message into an agent's interrupt stream.
    Returns the interrupt record.
    """
    if not _check_rate(agent):
        raise ValueError(f"Rate limit exceeded for agent '{agent}' ({RATE_LIMIT}/min)")

    record = {
        "id":       f"inj_{uuid.uuid4().hex[:8]}",
        "agent":    agent.lower(),
        "message":  message,
        "from":     from_,
        "ts":       time.time(),
        "task_ref": task_ref,
        "action":   None,
        "ack":      False,
    }

    r = _get_redis()
    if r:
        stream_key = f"dls.interrupts.{agent.lower()}"
        try:
            r.xadd(stream_key, {"data": json.dumps(record)}, maxlen=100)
            logger.info("Injected interrupt %s → %s: %s", record["id"], agent, message[:60])
        except Exception as e:
            logger.error("Redis inject failed: %s", e)
    else:
        logger.info("[log-only] Inject → %s: %s", agent, message[:60])

    return record


# ── Consume interrupts (agent side) ───────────────────────────────────────────

async def poll_interrupts(agent: str, since_id: str = "0") -> list[dict]:
    """
    Poll the interrupt stream for an agent. Call this between task steps.
    Returns list of pending interrupts (unacked).
    """
    r = _get_redis()
    if not r:
        return []

    stream_key = f"dls.interrupts.{agent.lower()}"
    try:
        results = r.xrange(stream_key, min=since_id, count=10)
        interrupts = []
        for entry_id, fields in results:
            try:
                record = json.loads(fields.get("data", "{}"))
                if not record.get("ack"):
                    interrupts.append({**record, "_stream_id": entry_id})
            except Exception:
                pass
        return interrupts
    except Exception as e:
        logger.error("Poll interrupts error: %s", e)
        return []


async def ack_interrupt(agent: str, interrupt_id: str, action: str):
    """Mark an interrupt as handled with a decision."""
    r = _get_redis()
    if not r:
        return
    # We don't have efficient update-in-stream, so just log the ack
    ack_key = f"dls.interrupts.{agent.lower()}.acks"
    try:
        r.hset(ack_key, interrupt_id, json.dumps({"action": action, "acked_at": time.time()}))
        logger.info("Acked interrupt %s with action=%s", interrupt_id, action)
    except Exception as e:
        logger.error("Ack interrupt error: %s", e)


# ── Telegram command handler ──────────────────────────────────────────────────
# Called by the Telegram bot when /inject /pause /resume /snapshot received

async def handle_telegram_command(cmd: str, args: str) -> str:
    """Parse and execute a Telegram interrupt command. Returns reply text."""
    parts  = args.strip().split(None, 1)
    agent  = parts[0].lower() if parts else ""
    rest   = parts[1] if len(parts) > 1 else ""

    VALID_AGENTS = {"dexter", "memo", "sienna", "nano", "david"}

    if cmd == "/inject":
        if not agent or agent not in VALID_AGENTS:
            return f"Usage: /inject <agent> <message>\nValid agents: {', '.join(sorted(VALID_AGENTS))}"
        if not rest:
            return "Usage: /inject <agent> <message>"
        try:
            rec = await inject(agent, rest, from_="dan")
            return f"✅ Injected into {agent}: `{rec['id']}`\n_{rest[:80]}_"
        except ValueError as e:
            return f"❌ {e}"

    elif cmd == "/pause":
        if not agent or agent not in VALID_AGENTS:
            return f"Usage: /pause <agent>"
        pause_agent(agent)
        return f"⏸ {agent.capitalize()} paused. Send /resume {agent} to continue."

    elif cmd == "/resume":
        if not agent or agent not in VALID_AGENTS:
            return f"Usage: /resume <agent>"
        resume_agent(agent)
        return f"▶️ {agent.capitalize()} resumed."

    elif cmd == "/snapshot":
        if not agent or agent not in VALID_AGENTS:
            return f"Usage: /snapshot <agent>"
        r = _get_redis()
        if r:
            key = f"dls.heartbeat.{agent}"
            data = r.hgetall(key)
            if data:
                return f"📸 {agent.capitalize()} snapshot:\n```\n{json.dumps(data, indent=2)[:400]}\n```"
        return f"No snapshot available for {agent}"

    return f"Unknown command: {cmd}"
