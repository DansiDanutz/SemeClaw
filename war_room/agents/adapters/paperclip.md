---
id: adapter_paperclip
name: Paperclip Adapter
adapter_kind: external_company
role: Bridge to a Paperclip-based company workspace
core: false
adapter:
  protocol: http
  base_url_env: PAPERCLIP_BASE_URL          # e.g. https://paperclip.example.com
  api_key_env: PAPERCLIP_API_KEY
  health_path: /api/health
  agents_path: /api/companies/{company_id}/agents
  invoke_path: /api/companies/{company_id}/agents/{agent_id}/invoke
  required_env: [PAPERCLIP_BASE_URL, PAPERCLIP_API_KEY, PAPERCLIP_COMPANY_ID]
---

# Paperclip Adapter

## What it does
Lets a SemeClaw meeting room call agents that live inside a Paperclip company workspace, as if they were native participants.

## Setup
```bash
export PAPERCLIP_BASE_URL=https://your-paperclip-instance.com
export PAPERCLIP_API_KEY=pk_live_...
export PAPERCLIP_COMPANY_ID=00000000-...
```
Then `semeclaw status` will show Paperclip as `connected`.

## Discovery
At meeting start, the adapter calls `GET {base}/api/companies/{company_id}/agents` and registers each remote agent with a `paperclip:` prefix. They appear in `/api/agents` alongside the local Research / Writer / Scraping / Coder / Browser core.

## Invocation
When the orchestrator routes a message to a `paperclip:` agent, the adapter forwards:
```
POST {base}/api/companies/{company_id}/agents/{agent_id}/invoke
{ "prompt": "...", "context": {...}, "meeting_id": "..." }
```
and streams the response back to the meeting.

## Failure mode
If the Paperclip instance is unreachable, the adapter unregisters its agents for the duration of the meeting and the orchestrator falls back to the local core 4.
