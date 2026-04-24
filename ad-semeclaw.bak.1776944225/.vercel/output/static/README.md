# Ad NOW — SemeClaw Advertiser Console

Vercel deployment for https://ad-semeclaw.vercel.app

## What it is
Static advertiser dashboard for buying 30-second pre-meeting ad spots inside SemeClaw meeting rooms. Auth via Supabase (Google / GitHub OAuth), payments via Stripe, card generation via the SemeClaw backend.

## Architecture
- **Frontend**: static HTML/JS served by Vercel.
- **API**: `/api/*` requests are rewritten to `https://semeclaw.fly.dev/api/*` (see `vercel.json`).
- **Auth**: Supabase JS client, initialized from `/api/advertiser/auth/config`.
- **Payments**: Stripe Checkout, session created server-side.

## Deploy

First time:
```bash
cd ad-semeclaw
npx vercel --prod
# link to / create the `ad-semeclaw` project, confirm scope
```

Subsequent deploys:
```bash
cd ad-semeclaw && npx vercel --prod
```

## Required env on the Fly backend (`semeclaw.fly.dev`)
```
DLS_TEAM_SUPABASE_URL
DLS_TEAM_SUPABASE_SERVICE_KEY
DLS_TEAM_SUPABASE_ANON_KEY
STRIPE_SECRET_KEY
STRIPE_WEBHOOK_SECRET
ADCLAW_PRICE_USD_10
ADCLAW_PRICE_USD_50
SEMECLAW_PUBLIC_URL=https://ad-semeclaw.vercel.app
ADCLAW_LLM_MODEL=anthropic/claude-haiku-4-5-20251001
ANTHROPIC_API_KEY   # or OPENROUTER_API_KEY
```

## CORS
`semeclaw.fly.dev` must allow `https://ad-semeclaw.vercel.app` as an origin.
Set `SEMECLAW_CORS_ORIGINS=https://ad-semeclaw.vercel.app` (comma-separated list if multiple).

## Supabase OAuth setup
In the Supabase dashboard → Authentication → URL Configuration, add:
- **Site URL**: `https://ad-semeclaw.vercel.app`
- **Redirect URLs**: `https://ad-semeclaw.vercel.app/**`

Then enable Google + GitHub providers with their respective client IDs.

## Stripe webhook
Point webhook endpoint at:
```
https://semeclaw.fly.dev/api/advertiser/webhook/stripe
```
Events to listen for: `checkout.session.completed`, `invoice.payment_succeeded`.
