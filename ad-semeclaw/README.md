# Ad NOW — SemeClaw Advertiser Console

Frontend for [ad-semeclaw.vercel.app](https://ad-semeclaw.vercel.app) — the SemeClaw War Room's advertiser cockpit.

Run 30-second pre-meeting ads inside SemeClaw. Sign in with Google or GitHub, top up credits, describe your project, generate a cinematic ad card, and toggle it into rotation.

## Files

| File | Purpose |
|---|---|
| `index.html` | Full SPA — login, card-based hub, wallet, profile, projects, library, subscriber spotlight. Vanilla JS + Supabase Auth. |
| `advertiser.html` | Physical copy of `index.html` so Vercel's `cleanUrls` resolves `/advertiser` after OAuth redirect. |
| `vercel.json` | Rewrites `/api/*` → `https://semeclaw.fly.dev/api/*` (Fly backend) + security headers. |
| `spotlight.json` | Data-driven subscriber spotlight — $50/mo subscribers get a 7-day featured slot. Nervix.ai is the anchor. |

## Architecture

```
Browser (ad-semeclaw.vercel.app)
  ├── /              → index.html                (login + hub)
  ├── /advertiser    → advertiser.html           (OAuth post-redirect)
  ├── /spotlight.json                            (featured projects)
  └── /api/*         → https://semeclaw.fly.dev  (Fly proxy via vercel.json rewrite)
                        ├── /api/advertiser/auth/config
                        ├── /api/advertiser/:id/wallet
                        ├── /api/advertiser/:id/projects
                        ├── /api/advertiser/:id/library
                        └── /api/advertiser/:id/slides/:id/*

Supabase project: gvuuauzsucvhghmpdpxf ("Memory")
  ├── Google OAuth (enabled)
  ├── GitHub OAuth (app: "Ad NOW - SemeClaw")
  └── Postgres: adclaw_advertisers, adclaw_campaigns, adclaw_slides, ...
```

## Design decisions

1. **Card-based hub** (not tabs). Post-login lands on a 4-card grid: Wallet · Profile · Projects · Library. Click a card → drill into its pane with a `← Hub` breadcrumb.
2. **Subscriber Spotlight** is separate from the user's own Projects — it's a promo surface. `spotlight.json` drives it. Square gradient logos + Playfair Display headline. `ttl_days` + `featured_from` control 7-day rotation. `pinned:true` pins forever (Nervix is the anchor).
3. **Motion** — conic-gradient borders animate on hover, cards lift, icons tilt, entrance staggered via `--delay` custom property.
4. **Fluid everywhere** — typography + padding + grid gaps use `clamp()` so the UI scales cleanly from 320px phones through 4K.
5. **Auth** — Supabase JS v2 OAuth → returns to `/advertiser` → session persists in localStorage.

## Subscriber Spotlight schema (`spotlight.json`)

```json
{
  "ttl_days": 7,
  "items": [
    {
      "id": "nervix",
      "name": "Nervix.ai",
      "tagline": "The AI Agent Marketplace",
      "description": "Hire, trust, and pay autonomous agents.",
      "url": "https://nervix.ai",
      "logo_letter": "N",
      "gradient_from": "#f59e0b",
      "gradient_to":   "#ec4899",
      "accent_rgb":    "245,158,11",
      "pinned": true,
      "featured_from": "2026-04-21T00:00:00Z"
    }
  ]
}
```

- `pinned: true` → stays forever (ignores TTL).
- `pinned: false` + `featured_from` → shown while `(now − featured_from) ≤ ttl_days`.
- Days-left pill turns amber in last 2 days, red when expired.

## Deploy

Connect this repo to the Vercel project `ad-semeclaw` (team iRISE). Pushes to `main` auto-deploy to production.

Env vars on Vercel: **none** — all config is server-side on Fly.io (`semeclaw.fly.dev`), which `vercel.json` proxies to.

## Required env on the Fly backend

```
DLS_TEAM_SUPABASE_URL=https://gvuuauzsucvhghmpdpxf.supabase.co
DLS_TEAM_SUPABASE_ANON_KEY
DLS_TEAM_SUPABASE_SERVICE_KEY
STRIPE_SECRET_KEY
STRIPE_WEBHOOK_SECRET
SEMECLAW_PUBLIC_URL=https://ad-semeclaw.vercel.app
SEMECLAW_CORS_ORIGINS=https://ad-semeclaw.vercel.app
ADCLAW_PRICE_USD_10
ADCLAW_PRICE_USD_50
ADCLAW_LLM_MODEL=anthropic/claude-haiku-4-5-20251001
ANTHROPIC_API_KEY      # or OPENROUTER_API_KEY
```

## Supabase OAuth setup (already configured)

Auth → URL Configuration:
- **Site URL**: `https://never-die.vercel.app` (legacy — don't change unless you re-test that app)
- **Redirect URLs (allow-list)** includes:
  - `https://ad-semeclaw.vercel.app/**`
  - `https://ad-semeclaw-*.vercel.app/**` (preview deploys)
  - `http://localhost:8765/**`, `http://localhost:8766/**`

OAuth providers:
- Google — client in `943890585767-i91o38hgsoeesk52c62it8hev4gcnrsa.apps.googleusercontent.com`
- GitHub — OAuth App `Ad NOW - SemeClaw` (ID 3547161), client `Ov23liVIDdNDIRIMjkaR`

## Stripe webhook

Point Stripe at `https://semeclaw.fly.dev/api/advertiser/webhook/stripe`.
Events: `checkout.session.completed`, `invoice.payment_succeeded`.

## Related

- War Room backend source: https://github.com/DansiDanutz/SemeClaw
- Fly deployment: https://semeclaw.fly.dev
