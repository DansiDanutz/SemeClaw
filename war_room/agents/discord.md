---
id: discord
name: Discord
adapter:
  protocol: http
  base_url_env: DISCORD_API_BASE
  api_key_env: DISCORD_BOT_TOKEN
  agents_path: /channels/{DISCORD_CHANNEL_ID}/messages
  required_env:
    - DISCORD_BOT_TOKEN
    - DISCORD_CHANNEL_ID
ingest:
  module: war_room.v1.discord_adapter
  callable: ingest
  marker: "!task "
writeback:
  module: war_room.v1.discord_adapter
  callable: writeback
---

# Discord adapter

A SemeClaw task source. Any message in the configured channel that begins
with `!task ` is ingested as an open task; the rest of the line becomes
the title and any subsequent text becomes the description. The
orchestrator's decision is written back as a threaded reply with
status + rationale.
