---
id: adapter_moltica
name: Moltica Adapter
adapter_kind: external_company
role: Bridge to a Moltica company workspace
core: false
adapter:
  protocol: http
  base_url_env: MOLTICA_BASE_URL
  api_key_env: MOLTICA_API_KEY
  health_path: /v1/health
  agents_path: /v1/workspaces/{workspace_id}/agents
  invoke_path: /v1/workspaces/{workspace_id}/agents/{agent_id}/run
  required_env: [MOLTICA_BASE_URL, MOLTICA_API_KEY, MOLTICA_WORKSPACE_ID]
---

# Moltica Adapter

## What it does
Lets a SemeClaw meeting room call agents that live inside a Moltica workspace.

## Setup
```bash
export MOLTICA_BASE_URL=https://api.moltica.example.com
export MOLTICA_API_KEY=mk_live_...
export MOLTICA_WORKSPACE_ID=ws_...
```

## Discovery
At meeting start the adapter calls `GET {base}/v1/workspaces/{workspace_id}/agents` and prefixes each remote agent id with `moltica:`.

## Invocation
```
POST {base}/v1/workspaces/{workspace_id}/agents/{agent_id}/run
{ "input": "...", "context": {...}, "stream": true }
```
Streaming SSE responses are forwarded into the meeting timeline.

## Failure mode
On 5xx or timeout, the adapter retries once with exponential backoff. After two failures it drops Moltica agents from the meeting and the orchestrator continues with whatever else is connected.
