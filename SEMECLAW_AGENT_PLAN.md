# SemeClaw Agent — Standalone + Embeddable Upgrade Plan

**Status:** In progress
**Owner:** Dan (CEO, NERVIX)
**Goal:** Turn SemeClaw from a self-hosted personal AI brain into a **deployable agent product** that other Paperclip companies and AI platforms (starting with NERVIX) can embed and consume.

---

## Why

Today SemeClaw runs as Dan's personal fleet brain on Mac Studio. The war-room dashboard — with its Meeting Library, voice-enabled scripted meetings, question recalibration, task re-analysis — is a unique product-shaped asset. Packaging it as a standalone agent unlocks:

1. **Distribution** — any Paperclip company can drop it into their ops stack
2. **NERVIX multi-tenancy** — customers get a full AI war-room inside the NERVIX marketplace
3. **Revenue** — per-seat or per-meeting pricing once the agent is consumable via API/embed

---

## Current Assets (already shipped)

| Module | Status | Location |
|--------|--------|----------|
| FastAPI dashboard | ✅ live on :8765 via launchd | `war_room/dashboard/server.py` |
| Meeting Skill (announcer + orchestrator + transitions) | ✅ | `war_room/dashboard/meeting_skill.py` |
| Meeting script / audio / finalize endpoints | ✅ | `server.py` (`/api/meeting/*`) |
| ElevenLabs Flash v2.5 → edge-tts fallback | ✅ | `/api/tts` |
| V1 flat + V2 cinematic orbital layouts | ✅ | `dashboard/index.html` |
| 48h retention + pin-to-save for reports + meetings | ✅ | `_prune_old()` |
| User-interjection budget (2 questions max) + recalibration | ✅ | `submitTMComment` + `/api/meeting/replan` |
| Finish-meeting → task re-analysis with VERDICT line | ✅ | `/api/meeting/finalize` |
| Paperclip bridge | ✅ partial | `war_room/paperclip_bridge.py` |
| WebSocket live updates | ✅ | `/ws` |

---

## Target Architecture

```
                    ┌──────────────────────────────────────────┐
                    │     SemeClaw Agent (standalone)          │
                    │  ┌────────────────────────────────────┐  │
                    │  │   Public HTTP API  (CORS-safe)     │  │
                    │  │  /api/agent/manifest               │  │
                    │  │  /api/agent/health                 │  │
                    │  │  /api/meeting/*                    │  │
                    │  │  /api/reports/*                    │  │
                    │  │  /api/tts                          │  │
                    │  └────────────────────────────────────┘  │
                    │  ┌────────────────────────────────────┐  │
                    │  │   Embed Layer                      │  │
                    │  │   /embed          (iframe-ready)   │  │
                    │  │   /embed.js       (JS SDK)         │  │
                    │  │   /embed/manifest.json             │  │
                    │  └────────────────────────────────────┘  │
                    │  ┌────────────────────────────────────┐  │
                    │  │   Auth + Tenant                    │  │
                    │  │   Bearer token                     │  │
                    │  │   X-Tenant-Id header               │  │
                    │  └────────────────────────────────────┘  │
                    └──────────────────────────────────────────┘
                                       ▲     ▲     ▲
                                       │     │     │
                ┌──────────────────────┘     │     └──────────────────┐
                │                            │                        │
        ┌───────┴────────┐           ┌───────┴────────┐       ┌───────┴────────┐
        │   NERVIX       │           │  Paperclip co's│       │  Direct embed  │
        │   (marketplace │           │  (ops tooling) │       │  (docs, CMS)   │
        │   integration) │           │                │       │                │
        └────────────────┘           └────────────────┘       └────────────────┘
```

---

## Phased Roadmap

### Phase 1 — Agent Manifest + Embed API (THIS COMMIT)
- [x] `/api/agent/manifest` — returns capabilities, endpoints, version, tenant info
- [x] `/embed` route — minimal chrome iframe-safe render of war-room
- [x] `/embed.js` — tiny JS SDK (`<script>` + `data-semeclaw-meeting="..."`)
- [x] CORS hardening — allow-list origins via env `SEMECLAW_CORS_ORIGINS`
- [x] Optional bearer auth — `SEMECLAW_API_KEY` env var, checked on `/api/meeting/*` write endpoints
- [x] Frame-ancestors CSP for iframe embed
- [x] Write `INTEGRATION.md` guide for consumers

### Phase 2 — Deployability (next)
- [ ] `Dockerfile` multi-stage build (Python 3.13 recommended for deploy parity; 3.10+ minimum)
- [ ] `docker-compose.yml` with optional Chroma + Redis
- [ ] GitHub Actions CI (lint + build)
- [ ] `.env.example` documenting every env var
- [ ] Release tag `v0.2.0` and publish image to `ghcr.io`

### Phase 3 — NERVIX Integration
- [ ] Register SemeClaw as a NERVIX marketplace agent card
- [ ] OAuth/API-key flow so each NERVIX user gets a scoped SemeClaw instance
- [ ] Webhook: on meeting finalize → POST back to NERVIX agent run record
- [ ] NERVIX UI: "Convene War Room" button in any task card → opens SemeClaw embed in modal

### Phase 4 — Paperclip Agent Adapter
- [ ] Register as a real Paperclip agent type (currently just bridged)
- [ ] Paperclip agent card with icon, description, pricing
- [ ] Paperclip webhook on meeting finalize → create Paperclip task note
- [ ] Bidirectional: SemeClaw can read Paperclip task context when launching meeting

### Phase 5 — Multi-tenancy + SaaS
- [ ] Per-tenant data isolation (reports/meetings namespaced by tenant_id)
- [ ] Per-tenant branding (logo, voice selection, colors)
- [ ] Usage metering (meetings/minute, TTS chars, LLM tokens)
- [ ] Billing hooks (Stripe metered subscription per tenant)

---

## Public API Contract (locked in Phase 1)

All endpoints return JSON unless noted.

### Agent identity
- `GET /api/agent/manifest` → full capability descriptor
- `GET /api/agent/health` → `{ok: true, uptime: ...}`

### Meetings
- `GET /api/reports` → list reports (rolling + saved)
- `GET /api/reports/content?name=` → markdown body
- `GET /api/meeting/script?name=&lang=en` → scripted segments
- `GET /api/meeting/audio?name=&download=` → MP3 (builds + caches)
- `POST /api/meeting/pin?name=` → move to saved/ (auth required)
- `POST /api/meeting/unpin?name=&file=` → move back (auth required)
- `POST /api/meeting/redirect` → `{responder, response}` for a user question
- `POST /api/meeting/replan` → rewrite remaining segments given Q&A
- `POST /api/meeting/finalize` → append Q&A + run verdict pass (auth required)

### TTS
- `GET /api/tts?text=&speaker=&lang=en` → audio/mpeg stream

### Embed
- `GET /embed?meeting=<report.md>&v=2&theme=dark` → embed-safe HTML
- `GET /embed.js` → JS SDK
- `GET /embed/manifest.json` → widget manifest

---

## Env Vars (Phase 1)

| Var | Purpose | Default |
|-----|---------|---------|
| `SEMECLAW_API_KEY` | Bearer token for write endpoints. Unset = open mode | unset |
| `SEMECLAW_CORS_ORIGINS` | Comma-sep origin allow-list | `*` |
| `SEMECLAW_FRAME_ANCESTORS` | CSP `frame-ancestors` — sets who can iframe us | `*` |
| `SEMECLAW_TENANT_ID` | Current tenant identifier (used in manifest) | `default` |
| `SEMECLAW_PUBLIC_URL` | External URL used in embed JS manifest | `http://127.0.0.1:8765` |
| `ELEVENLABS_API_KEY` | already in use | — |
| `OPENROUTER_API_KEY` | already in use | — |

---

## Integration Quickstart (for consumers)

### Embed in any web page
```html
<script src="https://semeclaw.yourdomain.com/embed.js"></script>
<div
  data-semeclaw-meeting="quarterly-review.md"
  data-semeclaw-theme="dark"
  data-semeclaw-v="2"
  style="width:100%; height:640px"
></div>
```

### Drive from a server (Python)
```python
import httpx
from os import environ

client = httpx.Client(
    base_url=environ["SEMECLAW_URL"],
    headers={"Authorization": f"Bearer {environ['SEMECLAW_API_KEY']}"},
)

# Generate a meeting audio for a report we authored
r = client.get("/api/meeting/audio", params={"name": "ops-review-2026-04-18.md"})
open("meeting.mp3", "wb").write(r.content)

# Pin (save) it
client.post("/api/meeting/pin", params={"name": "ops-review-2026-04-18.md"})

# Finalize with Q&A
client.post("/api/meeting/finalize", json={
  "name": "ops-review-2026-04-18.md",
  "qa_pairs": [{"question": "...", "responder": "GSD", "response": "..."}],
  "transcript": [...],
})
```

### NERVIX agent card (future, Phase 3)
```json
{
  "id": "semeclaw-war-room",
  "name": "War Room by SemeClaw",
  "description": "Convene a cinematic AI agent meeting on any task and get a recalibrated plan.",
  "icon": "🎭",
  "endpoint": "https://semeclaw-nervix.fly.dev",
  "pricing": {"model": "per_meeting", "cents": 25}
}
```

---

## Success Metrics

**Phase 1 (today):** Manifest endpoint returns, embed route renders in iframe, CORS works, integration doc complete.

**Phase 2:** Docker image builds + runs, GitHub Actions green, released tag.

**Phase 3:** NERVIX has a working SemeClaw card, at least 1 internal Dan's-Lab test user runs a meeting via NERVIX.

**Phase 4:** Paperclip has SemeClaw as a first-class agent, test meeting created from Paperclip task.

**Phase 5:** 2+ external Paperclip companies evaluating. Revenue path proven.
