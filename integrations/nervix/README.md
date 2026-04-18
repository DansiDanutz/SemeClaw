# NERVIX Integration — War Room by SemeClaw

**Status:** Draft — targets NERVIX marketplace v1 agent-card schema.
**Version:** matches SemeClaw `0.4.0`.

## What this folder contains

| File | Purpose |
|------|---------|
| `card.json` | NERVIX marketplace agent-card — uploaded to the NERVIX marketplace registry |
| `webhook_handler.example.ts` | Reference TypeScript handler that NERVIX deploys to receive `meeting.finalized` events |
| `README.md` | This file — how to install + flow diagrams |

## Install flow (NERVIX side)

```
┌────────────┐        ┌────────────┐        ┌──────────────┐
│   User     │ click  │   NERVIX   │ POST   │   SemeClaw   │
│            ├───────►│ marketplace├───────►│   endpoint   │
│            │        │            │        │              │
└────────────┘        └─────┬──────┘        └──────┬───────┘
                            │                      │
                            │ returns token        │ issues tenant token
                            │◄─────────────────────┤
                            │                      │
                            │ store per-user       │
                            │                      │
                            │   later... "Convene" │ POST /api/paperclip/trigger
                            ├─────────────────────►│
                            │                      │
                            │                      │ creates report,
                            │                      │ builds audio,
                            │                      │ returns embed_url
                            │◄─────────────────────┤
                            │                      │
                            │ open modal with embed_url
                            ▼                      │
                      ┌─────────────┐              │
                      │   <iframe>  │◄─────────────┤ serves /embed?meeting=...
                      └─────────────┘              │
                                                   │
                            (meeting runs)         │
                            (user closes)          │
                                                   │
                            ◄──── webhook POST ────┤ meeting.finalized +
                                  {verdict,qa,...} │ HMAC-SHA256 signature
                            │                      │
                            │ append to task.md    │
                            │ notify user          │
                            ▼
                    (task updated in NERVIX)
```

## Tenant identity

Every NERVIX user gets their own SemeClaw tenant:

- **Tenant ID format:** `nervix-user-{user_id}`
- **Header:** `X-Tenant-Id: nervix-user-12345`
- **Storage isolation:** reports land under `war_room/tenants/nervix-user-12345/research/`

## Webhooks — signature verification

SemeClaw HMACs the JSON body with the `secret` you provide at registration.

```ts
// TypeScript example — NERVIX side
import crypto from 'crypto';

function verifySignature(raw: string, header: string, secret: string): boolean {
  const [algo, sig] = header.split('=');
  if (algo !== 'sha256') return false;
  const expected = crypto
    .createHmac('sha256', secret)
    .update(raw)
    .digest('hex');
  return crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(expected));
}
```

## Triggering a meeting from NERVIX

```ts
const response = await fetch(`${SEMECLAW_URL}/api/paperclip/trigger`, {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${tenant.api_key}`,
    'X-Tenant-Id':   `nervix-user-${userId}`,
  },
  body: JSON.stringify({
    task_id:       task.id,
    task_title:    task.title,
    task_markdown: task.body,
    tenant_id:     `nervix-user-${userId}`,
    auto_audio:    true,
    webhook_url:   `${NERVIX_URL}/api/semeclaw/webhook`,
  }),
});

const { embed_url, share_url, audio_url } = await response.json();

// Open the modal with the embed iframe:
nervix.ui.openModal({
  title: `🎭 War Room — ${task.title}`,
  size:  { w: 1100, h: 720 },
  content: { type: 'iframe', src: embed_url },
});
```

## Live updates via SSE (optional)

NERVIX can stream live meeting events to its dashboard:

```ts
const es = new EventSource(`${SEMECLAW_URL}/api/events?tenant=nervix-user-${userId}`);
es.addEventListener('meeting.finalized', (e) => {
  const payload = JSON.parse(e.data);
  nervix.ui.toast(`✅ Task updated — ${payload.data.verdict_line}`);
});
```

## Uninstall

NERVIX should:
1. `DELETE /api/webhooks/{hook_id}` for all registered hooks for this user
2. Stop calling SemeClaw endpoints for this user

SemeClaw tenant data persists until an operator manually prunes it (`DELETE /api/reports?name=...` per report).
