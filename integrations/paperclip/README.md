# Paperclip Integration — First-class Agent Adapter

**Version:** matches SemeClaw `0.4.0`.
**Status:** Live (Phase 4 endpoints shipped).

## What it is

SemeClaw is registered as a **native agent type** on any Paperclip control plane:

```
agent_type: semeclaw.war-room
```

When a Paperclip task wants AI deliberation, it calls `POST /api/paperclip/trigger` with the task content. SemeClaw:

1. Creates a report scoped to the task's tenant
2. Generates the meeting MP3 (optional, default on)
3. Issues a 30-day share URL
4. Registers a one-off webhook to POST back when the meeting finalizes
5. Returns `embed_url` + `audio_url` + `share_url` + `script_url`

## Agent-card manifest

Fetch the live card at:

```
GET {SEMECLAW_URL}/api/paperclip/agent-card
```

```json
{
  "agent_type":   "semeclaw.war-room",
  "version":      "0.4.0",
  "name":         "War Room by SemeClaw",
  "icon":         "🎭",
  "triggers":     ["on_task_comment", "on_task_status_change:review", "manual_convene_meeting"],
  "input_schema": { "task_id": "string", "task_markdown": "string", ... },
  "output_schema":{ "meeting_id": "string", "audio_url": "string", ... },
  "pricing_hint": {"model": "per_meeting", "est_cents": 25}
}
```

## Trigger endpoint

```bash
curl -X POST "$SEMECLAW_URL/api/paperclip/trigger" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: acme-corp" \
  -d '{
    "task_id":       "PC-1234",
    "task_title":    "Q2 pricing decision",
    "task_markdown": "# Q2 pricing\n\n## Research\n\nCompetitors at $49\n\n## Strategist\n\nGo mid-tier at $39\n",
    "tenant_id":     "acme-corp",
    "auto_audio":    true,
    "webhook_url":   "https://paperclip.acme-corp.com/api/agents/semeclaw/callback"
  }'
```

Response:

```json
{
  "ok": true,
  "paperclip_task_id": "PC-1234",
  "report_name": "pc-pc-1234.md",
  "meeting_id": "pc-pc-1234",
  "audio_url": "https://semeclaw.../api/meeting/audio?name=pc-pc-1234.md",
  "embed_url": "https://semeclaw.../embed?meeting=pc-pc-1234.md&v=2",
  "share_url": "https://semeclaw.../share/a1b2c3d4e5f6g7h8",
  "script_url": "https://semeclaw.../api/meeting/script?name=pc-pc-1234.md",
  "manifest_url": "https://semeclaw.../api/paperclip/agent-card",
  "webhook_registered": true
}
```

## Control plane registration (Paperclip side)

If Paperclip exposes a public registry, register SemeClaw:

```bash
# Paperclip CLI or REST
paperclip agents register \
  --card-url="https://semeclaw.example.com/api/paperclip/agent-card" \
  --mode=adapter
```

Or drop the JSON card into your Paperclip config.

## Webhook callback contract

When the meeting finalizes, SemeClaw POSTs:

```json
{
  "event": "meeting.finalized",
  "ts": "2026-04-18T09:00:00Z",
  "agent_version": "0.4.0",
  "tenant_id": "acme-corp",
  "data": {
    "name": "pc-pc-1234.md",
    "verdict_line": "VERDICT: CORRECT — proceed",
    "qa_count": 1
  }
}
```

Headers:
- `X-SemeClaw-Event: meeting.finalized`
- `X-SemeClaw-Signature: sha256=<hmac>` (if secret was provided)

## What the existing paperclip_bridge.py does

`war_room/paperclip_bridge.py` is the **outbound** bridge — SemeClaw calls into the Paperclip REST API to list agents, sync health, etc.

`/api/paperclip/agent-card` + `/api/paperclip/trigger` are the **inbound** side — Paperclip calls into SemeClaw to convene meetings.

Together they make SemeClaw a bidirectional Paperclip agent.

## Next: native rendering inside Paperclip UI

When Paperclip adds the `agent_embed_iframe` capability to its UI, NERVIX/Paperclip users will see the meeting modal directly inside their task card. Currently the meeting opens via `embed_url` in a new tab.
