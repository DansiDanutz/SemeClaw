# Slack Integration — SemeClaw War Room

**Version:** SemeClaw 0.6.0+
**Goal:** Let any Slack workspace convene a SemeClaw meeting from a message or a thread.

## What you get

- `/semeclaw <subject>` slash command — generates a quick task markdown from your text and convenes a meeting
- `/semeclaw-thread` — summarizes the current thread into a report and convenes a meeting
- Post-meeting unfurl — pastes the verdict line + audio link back in thread

## Setup

### 1. Create a Slack app

Go to https://api.slack.com/apps → Create New App → From scratch.

Required **OAuth scopes** (Bot Token Scopes):
- `chat:write`
- `commands`
- `channels:history`
- `groups:history`
- `files:write`

### 2. Register slash commands

- `/semeclaw` — Request URL: `https://your-slack-bot.example.com/slack/command`
- `/semeclaw-thread` — same URL

### 3. Event Subscriptions

Subscribe to `message.channels` if you want the thread-summarizer.

### 4. Deploy the bot handler

See `bot.py` in this folder. Runs on any HTTP framework. Reference uses Flask.

## Bot handler

```python
# Install: pip install flask slack-bolt httpx
# Env required:
#   SLACK_BOT_TOKEN        — xoxb-...
#   SLACK_SIGNING_SECRET   — from Slack app basic info
#   SEMECLAW_URL           — https://semeclaw.your-host.com
#   SEMECLAW_API_KEY       — bearer token for write endpoints
```

See `bot.py` for the full reference implementation.

## Flow

```
User: /semeclaw What's our Q2 go-to-market?
  ↓
Slack sends webhook → bot.py
  ↓
bot.py builds markdown:
  # What's our Q2 go-to-market?
  **Task:** What's our Q2 go-to-market?
  ## Research Agent
  [Slack context]
  ## Strategist Agent
  [invites analysis]
  ## Writer Agent
  [ask for narrative]
  ↓
POST $SEMECLAW_URL/api/reports (returns {name})
POST $SEMECLAW_URL/api/meetings/{name}/share (returns {url})
  ↓
Post back to Slack:
  🎭 War Room convened · Listen: {share_url}
```

When the meeting finalizes, SemeClaw webhooks back → bot posts verdict in thread.

## Register the webhook

Point SemeClaw at your bot's callback:

```bash
curl -X POST https://semeclaw.example.com/api/webhooks \
  -H "Authorization: Bearer $SEMECLAW_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://your-slack-bot.example.com/slack/semeclaw-webhook",
    "events": ["meeting.finalized"],
    "secret": "your-shared-secret"
  }'
```

Verify signature with HMAC-SHA256 on `X-SemeClaw-Signature`.
