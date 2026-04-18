# SemeClaw — OpenClaw Integration Guide

SemeClaw exposes a full HTTP API that OpenClaw agents can call directly.
The War Room runs at `http://127.0.0.1:8765` on Mac Studio.

## Starting a Pipeline

```bash
curl -X POST http://127.0.0.1:8765/api/run \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $SEMECLAW_API_KEY" \
  -d '{"task": "Build landing page for NERVIX", "project": "nervix"}'
```

## Checking Fleet Health

```bash
curl http://127.0.0.1:8765/api/agent/health | jq '.system_health'
```

## Recording Task Completion (KPI)

```bash
# POST to collector — agent signals completion
curl -X POST http://127.0.0.1:8765/api/agent/run-complete \
  -H "Content-Type: application/json" \
  -d '{"agent_name": "Dexter", "task_ref": "nervix-deploy-v2", "status": "success", "tokens_used": 45000}'
```

## Sending a Human Interrupt

```bash
# Mid-meeting override from Telegram or CLI
curl -X POST http://127.0.0.1:8765/api/meeting/inject \
  -H "Content-Type: application/json" \
  -d '{"message": "Focus on mobile-first, skip desktop for now", "agent": "Orchestrator"}'
```

## Manifest Discovery

```bash
curl http://127.0.0.1:8765/api/agent/manifest | jq '.capabilities'
```

## Redis Streams (direct)

OpenClaw agents can publish results directly to Redis:
```python
import redis, json
r = redis.Redis(host="localhost", port=6379)
r.xadd("dls.results", {"data": json.dumps({
    "project": "nervix",
    "agent": "Dexter",
    "status": "success",
    "tokens_used": 12000,
    "cost_usd": 0.042
})})
```

## Sentinel Health API

```bash
curl http://127.0.0.1:18790/probes | jq '.droplets'
```

## Coordinator (LLM Circuit-Breaker)

Point any OpenClaw agent at the coordinator instead of direct LLM APIs:
```
ANTHROPIC_BASE_URL=http://127.0.0.1:8996
```
The coordinator auto-fails over across 8 backends.
