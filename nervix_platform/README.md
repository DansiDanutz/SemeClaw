# NERVIX Platform

AI Agent Marketplace with Stripe credit purchases, project dashboard, and ad-play ingestion.

## Quick Start

```bash
cd nervix_platform
pip install fastapi uvicorn jinja2 stripe python-dotenv pydantic email-validator
python main.py
```

Or with uv:
```bash
uv run --with fastapi --with uvicorn --with jinja2 --with stripe --with python-dotenv --with pydantic --with email-validator python main.py
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `STRIPE_SECRET_KEY` | Stripe secret key (test mode) | `sk_test_` |
| `STRIPE_PUBLISHABLE_KEY` | Stripe publishable key | `pk_test_` |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook endpoint secret | (empty) |

## Features

- **Member sign-up** — email + name registration
- **Credit purchase** — Stripe Checkout integration (Starter $5, Pro $19, Team $49)
- **Project submission** — title, description, URL
- **Dashboard** — live stats (projects, ad plays, clicks, credits)
- **Ad play ingestion** — POST `/api/ad-play` deducts credits per play
- **Stripe webhooks** — auto-credit on payment success

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Landing page |
| GET | `/signup` | Sign-up form |
| POST | `/api/members` | Create member |
| GET | `/dashboard?member_id=...` | Member dashboard |
| POST | `/api/projects` | Submit project |
| POST | `/api/checkout` | Create Stripe checkout |
| POST | `/api/webhooks/stripe` | Stripe webhook |
| POST | `/api/ad-play` | Ingest ad play event |
| GET | `/api/health` | Health check |

## Testing Stripe

1. Get test keys from https://dashboard.stripe.com/test/apikeys
2. Set `STRIPE_SECRET_KEY` and `STRIPE_PUBLISHABLE_KEY`
3. Use Stripe test card: `4242 4242 4242 4242`, any future date, any CVC
4. For webhooks locally: `stripe listen --forward-to localhost:8001/api/webhooks/stripe`
