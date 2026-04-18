# SemeClaw Agent — Enhancement Roadmap

This is the wish-list for turning SemeClaw from "v0.2.0 embeddable agent" into a full-blown SaaS product. Items are grouped by category and priority.

Legend: 🔴 Critical · 🟡 High-value · 🟢 Nice-to-have · 🔵 Experimental · ✅ Shipped

---

## ✅ Delivered in v0.4.0 (2026-04-18 evening)

- ✅ **SSE events** — `GET /api/events?tenant=&events=` streams every lifecycle event live. NERVIX + Paperclip UIs can subscribe via `EventSource`. 20s keepalive, tenant + event-name filtering.
- ✅ **NERVIX marketplace card** — `integrations/nervix/card.json` (full manifest), `README.md` (install flow), `webhook_handler.example.ts` (TypeScript handler with signature verification).
- ✅ **Paperclip first-class agent** — `GET /api/paperclip/agent-card` returns native manifest; `POST /api/paperclip/trigger` one-shot creates report + audio + share link + registers one-off webhook for the finalize callback.
- ✅ **Theater mode (V3)** — `🎬 Theater` button in header adds `.theater-layout`; current speaker scales 1.85×, others dim, subtitle strip below. Perfect for demos/webinars.
- ✅ Bumped manifest + headers + Dockerfile + pyproject.toml to `0.4.0`

## ✅ Delivered in v0.3.0 (2026-04-18)

- ✅ `POST /api/reports` — external systems (NERVIX, Paperclip) can create reports
- ✅ `POST /api/reports/upload` — multipart `.md` file upload
- ✅ `DELETE /api/reports?name=` — cleanup endpoint
- ✅ **X-Tenant-Id header + per-tenant isolation** — reports namespaced under `war_room/tenants/<id>/`
- ✅ **Webhooks** — `POST /api/webhooks` register, list, delete; fires on `report.created`, `report.deleted`, `meeting.finalized` with HMAC SHA-256 signature header
- ✅ **`/metrics` Prometheus** — counters for meetings, questions, TTS, reports, webhooks
- ✅ **Share links** — `POST /api/meetings/{name}/share` → `/share/{token}` public playback URLs (30d TTL)
- ✅ **GitHub Actions CI** — lint + meeting_skill smoke test + server boot check + Docker build + optional release push to ghcr.io on `v*` tag

---

## 1. Public API Completeness

### 🔴 `POST /api/reports` — accept new reports
Today reports are dropped into `war_room/research/*.md` by internal pipelines only. Consumers (NERVIX, Paperclip tasks) need a way to CREATE a report through the API.
- Body: `{name, content, tenant_id?}`
- Response: `{name, url, audio_url}`
- Auth: required (write endpoint)
- Side effect: optionally auto-generates the meeting audio immediately

### 🔴 `POST /api/reports/upload` — multipart upload
Same as above but accepts raw `.md` or `.txt` file via multipart form.

### 🟡 `DELETE /api/reports?name=` — clean up
Delete a report + its cached audio. Already exists for pruning, expose it.

### 🟡 `GET /api/meetings/:id/events` — server-sent events
Stream live meeting progress to consumers:
```
event: speaker-change
data: {"speaker":"GSD","text":"..."}

event: question-asked
data: {"question":"...","responder":"David"}

event: meeting-finalized
data: {"verdict":"...","updated_markdown_url":"..."}
```

### 🟢 `GET /api/meetings/:id/transcript.srt` — subtitle export
Generate SRT from the meeting script + audio duration. Enables embedding on video platforms.

---

## 2. Webhooks

### 🔴 `POST /api/webhooks` — register webhook
Consumers register callback URLs for lifecycle events:
- `meeting.started`
- `meeting.speaker_changed`
- `meeting.question_asked`
- `meeting.finalized`
- `meeting.pinned`

SemeClaw POSTs JSON payloads with a signed `X-SemeClaw-Signature` HMAC header.

### 🟡 Webhook retry + backoff
Queue failed deliveries in-memory (or Redis) with exponential backoff for 24h.

---

## 3. Multi-tenancy

### 🔴 Per-tenant data isolation
Every report + meeting audio stored under `tenants/{tenant_id}/…`. Requires:
- `X-Tenant-Id` header on all requests
- Tenant-scoped auth tokens (one per tenant, rotating)
- Per-tenant retention config (some may want 7-day, others 48h)

### 🟡 Tenant provisioning endpoint
`POST /api/tenants` — creates tenant, returns API key. Used by NERVIX when a user installs the SemeClaw marketplace card.

### 🟡 Per-tenant branding
Logo URL, accent color, preferred voice for orchestrator, custom agent names.

### 🟢 Tenant usage metrics
`GET /api/tenants/:id/usage` — meetings this month, TTS chars, LLM tokens, storage MB.

---

## 4. Voice & TTS

### 🟡 Voice cloning endpoint
`POST /api/voices` — accept 30s reference audio + transcript, register a new custom voice. Backed by either ElevenLabs Instant Voice Clone API or VoxCPM2 on an on-demand GPU droplet.

### 🟡 Per-agent voice override
Let consumers remap `{speaker → voice_id}` for their tenant. UI-driven voice picker.

### 🟢 Voice preview endpoint
`GET /api/voices/:id/preview` — returns a 5-second sample MP3.

### 🔵 Streaming TTS (low-latency)
Stream audio chunks as they're generated instead of buffering the whole MP3. Cuts first-word latency from ~2s to ~200ms.

---

## 5. UI / UX Enhancements

### 🟡 Theater mode
Fullscreen the current speaker's avatar + subtitle. Best for "watching a meeting" in a shared TV/projector setting.

### 🟡 Compact mode
Minimal header + chat only, no avatars/table. For embedding in narrow sidebars.

### 🟡 Presentation mode
Fullscreen with large typography + fewer controls. For demos/webinars.

### 🟢 Meeting templates
Pre-built meeting scripts for common workflows: "Quarterly Review", "Bug Triage", "Sprint Planning", "Customer Interview", "Post-Mortem". Consumers pick a template → populate with their data → convene.

### 🟢 Agent persona editor
In-UI form: name, avatar emoji, voice, role description, color. Saved per-tenant.

### 🟢 Dark + light themes
Currently dark only. Add light mode + system-preference detection.

### 🔵 3D cinematic mode
Three.js-based room with actual 3D avatars sitting around a proper table. The reference image Dan showed is achievable with a simple Three.js scene.

---

## 6. Export & Sharing

### 🟡 `GET /api/meetings/:id/pdf` — transcript PDF
Formatted PDF with agent color-coding + Q&A highlighted + verdict line.

### 🟡 Share links
`POST /api/meetings/:id/share` → returns `{url: https://share.semeclaw.../abc123}`. Anyone with the link can listen to the meeting audio + read transcript (no auth required, expires in 30 days default).

### 🟡 MP4 video export
Render a simple video: speaker's avatar fullscreen + subtitles + audio. Great for Twitter/LinkedIn clips.

### 🟢 OpenGraph preview images
Auto-generate a meeting preview image (title + agents + quote from transcript) for link unfurl on Slack/Discord/Twitter.

---

## 7. Integrations

### 🔴 NERVIX marketplace card — Phase 3 in plan
See `SEMECLAW_AGENT_PLAN.md`.

### 🔴 Paperclip first-class agent — Phase 4 in plan
See `SEMECLAW_AGENT_PLAN.md`.

### 🟡 Slack bot
`/semeclaw summarize-thread` → creates a report from Slack thread messages → generates meeting → posts audio + transcript back.

### 🟡 GitHub Action
`dansidanutz/semeclaw-action@v1` — on PR open, generate a meeting about the diff, post audio + verdict to PR comment.

### 🟡 Discord bot
Same idea as Slack.

### 🟢 Linear integration
On issue creation, create a "War Room Check" — quick meeting that asks research/strategist/writer to validate the issue is worth solving.

### 🟢 Notion integration
Right-click any Notion page → "Convene War Room".

### 🔵 Zapier + Make integration
Trigger meetings from any app that supports webhooks.

---

## 8. Observability & Ops

### 🟡 Prometheus metrics endpoint
`GET /metrics` — standard Prometheus exposition format:
- `semeclaw_meetings_total`
- `semeclaw_questions_asked_total`
- `semeclaw_tts_chars_total`
- `semeclaw_llm_tokens_total`
- `semeclaw_meeting_duration_seconds{quantile="0.5"}`

### 🟡 Structured logs (JSON)
Ship logs via stdout in JSON, let consumers pipe to Datadog/Loki.

### 🟢 Audit log
Track every write endpoint call with tenant, user, action, timestamp. Expose `/api/audit` for compliance.

### 🟢 Cost ledger
Track per-tenant ElevenLabs + OpenRouter costs, expose via `/api/tenants/:id/costs`.

---

## 9. Deployment & CI

### 🔴 Docker image pushed to `ghcr.io` — Phase 2 in plan
Tag `v0.2.0`, `v0.2.1`, `latest`.

### 🔴 GitHub Actions CI
- Lint (ruff)
- Type check (mypy or pyright)
- Unit tests (pytest)
- Docker build + push on release tag

### 🟡 Helm chart for Kubernetes
For consumers running their own SemeClaw cluster.

### 🟡 Fly.io one-click deploy
`fly launch` template — Dan's cheapest path to host a production SemeClaw for NERVIX/Paperclip customers.

### 🟢 Cloudflare Workers edge layer
Cache `/embed.js`, `/embed/manifest.json`, and maybe `/api/meeting/audio` responses globally. First-byte time <100ms worldwide.

---

## 10. SaaS Business Layer — Phase 5

### 🟡 Stripe metered billing
- Free tier: 10 meetings/month, 5 min each
- Pro: $49/mo, unlimited meetings, 30 min each
- Enterprise: custom — unlimited, priority support, SSO

### 🟡 Admin dashboard
Separate UI at `/admin` for Dan to manage tenants, usage, revenue, churn.

### 🟢 Referral program
Every Paperclip company that integrates gets affiliate revenue share.

### 🟢 White-label option
Let enterprises rebrand SemeClaw entirely — custom domain, logo, no "Powered by" footer.

---

## Prioritized Sequence (suggested next 4 weeks)

| Week | Theme | Items |
|------|-------|-------|
| **Week 1** | Docker + CI | Dockerfile push to ghcr.io, GitHub Actions, release `v0.2.0` tag |
| **Week 2** | Ingest + Events | `POST /api/reports`, webhook registration, SSE event stream |
| **Week 3** | Multi-tenancy | tenant isolation, provisioning, per-tenant branding, storage namespacing |
| **Week 4** | NERVIX card | marketplace card JSON, embed modal, webhook → NERVIX task update |

After week 4 SemeClaw is genuinely ready for first external Paperclip company to pilot.

---

## What NOT to build (yet)

- Mobile native apps — iframe embed inside a WebView is good enough
- On-device inference — cloud TTS is high enough quality, latency is acceptable
- Enterprise SSO — don't need it until first enterprise prospect asks
- Per-agent custom LLMs — single OpenRouter gateway is sufficient for now

Focus on **what gets SemeClaw into NERVIX and 1-2 Paperclip companies first**.
