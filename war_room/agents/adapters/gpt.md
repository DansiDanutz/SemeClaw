---
id: adapter_gpt
name: GPT Adapter
adapter_kind: external_api
role: Bridge to OpenAI GPT models
core: false
adapter:
  protocol: http
  base_url: https://api.openai.com/v1
  api_key_env: OPENAI_API_KEY
  health_path: /models
  invoke_path: /chat/completions
  default_model: gpt-4o-mini
  required_env: [OPENAI_API_KEY]
---

# GPT Adapter

## What it does
Lets the SemeClaw orchestrator route a turn to OpenAI's GPT models when the user wants premium quality on a specific message. Configurable per-meeting — never used by default in the OSS demo (free-only).

## Setup
```bash
export OPENAI_API_KEY=sk-...
# Optional model override (default: gpt-4o-mini)
export GPT_ADAPTER_MODEL=gpt-4o
```

## Discovery
Adapter health-checks `GET /v1/models` at startup. If 200, registers a `gpt:assistant` agent that can be summoned by the user via `@gpt` mention or by the orchestrator for tier-pro meetings.

## Invocation
Standard OpenAI Chat Completions:
```
POST /v1/chat/completions
{
  "model": "gpt-4o-mini",
  "messages": [{"role":"system","content":"..."},{"role":"user","content":"..."}],
  "temperature": 0.7
}
```

## Cost guardrails
- Per-meeting cap: `GPT_ADAPTER_MAX_TOKENS_PER_MEETING` (default 50,000)
- Model defaults to `gpt-4o-mini` (cheapest tier)
- The CLI `semeclaw status` shows estimated monthly spend

## Failure mode
On 401/429, the adapter unregisters and the orchestrator falls back to the free OpenRouter waterfall.
