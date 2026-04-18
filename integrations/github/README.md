# GitHub Action — SemeClaw War Room

**Version:** SemeClaw 0.6.0+
**Goal:** Auto-convene an AI war-room meeting on every PR, posting the transcript + verdict back as a PR comment.

## What it does

1. Pulls the PR title + body into a markdown task
2. Adds Research / Strategist / Writer agent prompts
3. POSTs to your SemeClaw deployment via `/api/reports`
4. Generates meeting audio
5. Creates a 30-day public share link
6. Posts a comment on the PR with the audio + transcript + embed URLs

## Setup

In your repo, add `.github/workflows/semeclaw.yml`:

```yaml
name: SemeClaw War Room

on:
  pull_request:
    types: [opened, reopened]

jobs:
  war-room:
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write
      contents: read
    steps:
      - uses: DansiDanutz/SemeClaw/integrations/github@v0.6.0
        with:
          semeclaw-url:     ${{ secrets.SEMECLAW_URL }}
          semeclaw-api-key: ${{ secrets.SEMECLAW_API_KEY }}
          comment-on-pr:    true
```

Add two repo secrets:
- `SEMECLAW_URL` — e.g. `https://semeclaw.fly.dev`
- `SEMECLAW_API_KEY` — your bearer token

## Inputs

| Input | Default | Purpose |
|-------|---------|---------|
| `semeclaw-url` | — | Base URL of your SemeClaw deployment (required) |
| `semeclaw-api-key` | — | Bearer token for write endpoints (required) |
| `task-title` | PR/issue title | Override the task title |
| `context` | `""` | Extra markdown appended to the report |
| `comment-on-pr` | `true` | Post a PR comment with the meeting links |

## Outputs

| Output | Description |
|--------|-------------|
| `report-name` | SemeClaw report filename |
| `embed-url` | Iframe embed URL |
| `share-url` | Public share URL (30d TTL) |
| `verdict-line` | `VERDICT:` line (populated after the meeting finalizes — use a second job that waits on the webhook) |

## Tenant isolation

The action sends `X-Tenant-Id: gh-<owner>` so your GitHub org's meetings stay isolated from other tenants.

## Want the verdict in the PR comment?

Register a webhook on SemeClaw pointing to your GitHub Deployment Bot or a lightweight serverless function that posts back:

```bash
curl -X POST $SEMECLAW_URL/api/webhooks \
  -H "Authorization: Bearer $SEMECLAW_API_KEY" \
  -d '{"url":"https://your-bot/gh-webhook","events":["meeting.finalized"],"secret":"..."}'
```

On receipt, call `gh pr comment` with the verdict.
