"""
SemeClaw Slack bot — reference implementation.

Provides two slash commands:
  /semeclaw <subject>    — convene a meeting with a fresh subject
  /semeclaw-thread       — convene a meeting from the current thread context

Plus a webhook receiver that listens for SemeClaw meeting.finalized events
and posts the verdict line back in the originating Slack thread.

Deploy:
  pip install flask slack-bolt slack-sdk httpx
  export SLACK_BOT_TOKEN=xoxb-...
  export SLACK_SIGNING_SECRET=...
  export SEMECLAW_URL=https://semeclaw.your-host.com
  export SEMECLAW_API_KEY=...
  python bot.py
"""
from __future__ import annotations

import hmac
import hashlib
import json
import os
from typing import Any

import httpx
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request, jsonify, Response

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SLACK_BOT_TOKEN      = os.environ["SLACK_BOT_TOKEN"]
SLACK_SIGNING_SECRET = os.environ["SLACK_SIGNING_SECRET"]
SEMECLAW_URL         = os.environ.get("SEMECLAW_URL", "http://localhost:8765").rstrip("/")
SEMECLAW_API_KEY     = os.environ.get("SEMECLAW_API_KEY", "")
SEMECLAW_SHARED_SECRET = os.environ.get("SEMECLAW_SHARED_SECRET", "")

# In-memory map: meeting report_name → slack thread context so we can post back
_PENDING: dict[str, dict[str, str]] = {}

bolt_app = App(token=SLACK_BOT_TOKEN, signing_secret=SLACK_SIGNING_SECRET)
flask_app = Flask(__name__)
handler = SlackRequestHandler(bolt_app)


def _semeclaw(method: str, path: str, **kw) -> httpx.Response:
    headers = kw.pop("headers", {}) or {}
    if SEMECLAW_API_KEY:
        headers["Authorization"] = f"Bearer {SEMECLAW_API_KEY}"
    kw["headers"] = headers
    return httpx.request(method, f"{SEMECLAW_URL}{path}", timeout=20.0, **kw)


def _build_markdown(subject: str, bullets: list[str]) -> str:
    return (
        f"# {subject}\n\n"
        f"**Task:** {subject}\n"
        f"**Via:** Slack bot\n\n"
        f"## Research Agent\n\n"
        f"Context captured from Slack:\n\n" +
        "\n".join(f"- {b}" for b in bullets) +
        "\n\n"
        f"## Strategist Agent\n\n"
        f"Synthesize the above into the 3 most likely strategic paths, tradeoffs, and pick one with conviction.\n\n"
        f"## Writer Agent\n\n"
        f"Draft the 3-sentence public-facing summary + a 3-bullet action list.\n"
    )


# ---------------------------------------------------------------------------
# Slash command: /semeclaw <subject>
# ---------------------------------------------------------------------------
@bolt_app.command("/semeclaw")
def handle_semeclaw(ack, body, respond, logger):
    ack()
    subject = (body.get("text") or "").strip() or "Ad-hoc war room"
    channel_id = body["channel_id"]
    user_id = body["user_id"]

    respond(f"🎭 Convening War Room: *{subject}* …")

    md = _build_markdown(subject, [f"Called by <@{user_id}> in <#{channel_id}>"])
    try:
        r = _semeclaw("POST", "/api/reports", json={
            "task": subject, "content": md, "auto_audio": True,
        })
        r.raise_for_status()
        report = r.json()
        s = _semeclaw("GET", f"/api/meeting/share?name={report['name']}").json()
    except Exception as e:
        respond(f"⚠️ Failed: {e}")
        return

    _PENDING[report["name"]] = {"channel": channel_id, "user": user_id}

    respond({
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn",
                "text": f"🎭 *War Room convened* — _{subject}_"}},
            {"type": "section", "text": {"type": "mrkdwn",
                "text": f":headphones: <{s['url']}|Listen to the meeting>\n:memo: <{SEMECLAW_URL}/api/reports/content?name={report['name']}|View report>"}},
            {"type": "context", "elements": [
                {"type": "mrkdwn", "text": f"Meeting `{report['name']}` · I'll post the verdict here when it closes."}
            ]},
        ],
        "response_type": "in_channel",
    })


# ---------------------------------------------------------------------------
# Slash command: /semeclaw-thread
# ---------------------------------------------------------------------------
@bolt_app.command("/semeclaw-thread")
def handle_thread(ack, body, respond, client, logger):
    ack()
    channel_id = body["channel_id"]
    user_id = body["user_id"]
    # Last 20 messages in the channel as context
    try:
        hist = client.conversations_history(channel=channel_id, limit=20)
        msgs = hist.get("messages", [])
    except Exception as e:
        respond(f"⚠️ Couldn't read channel: {e}")
        return

    bullets = []
    for m in reversed(msgs):
        txt = (m.get("text") or "").replace("\n", " ")[:200]
        uid = m.get("user") or "?"
        if txt:
            bullets.append(f"<@{uid}>: {txt}")

    subject = (body.get("text") or "").strip() or "Summarize this thread"
    md = _build_markdown(subject, bullets)

    try:
        r = _semeclaw("POST", "/api/reports", json={
            "task": subject, "content": md, "auto_audio": True,
        })
        r.raise_for_status()
        report = r.json()
        s = _semeclaw("GET", f"/api/meeting/share?name={report['name']}").json()
    except Exception as e:
        respond(f"⚠️ Failed: {e}")
        return

    _PENDING[report["name"]] = {"channel": channel_id, "user": user_id}
    respond({
        "response_type": "in_channel",
        "text": f"🎭 Thread-war-room convened — <{s['url']}|Listen>",
    })


# ---------------------------------------------------------------------------
# Inbound webhook from SemeClaw when a meeting finalizes
# ---------------------------------------------------------------------------
def _verify_semeclaw_signature(raw: bytes, header: str | None) -> bool:
    if not SEMECLAW_SHARED_SECRET or not header:
        return not SEMECLAW_SHARED_SECRET  # allow unsigned if no secret
    parts = header.split("=")
    if len(parts) != 2 or parts[0] != "sha256":
        return False
    expected = hmac.new(SEMECLAW_SHARED_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(parts[1], expected)


@flask_app.post("/slack/semeclaw-webhook")
def semeclaw_webhook():
    raw = request.get_data()
    if not _verify_semeclaw_signature(raw, request.headers.get("X-SemeClaw-Signature")):
        return Response("bad signature", status=401)

    body: dict[str, Any] = json.loads(raw)
    if body.get("event") != "meeting.finalized":
        return jsonify(ok=True, skipped=body.get("event"))

    data = body.get("data") or {}
    name = data.get("name", "")
    pending = _PENDING.pop(name, None)
    if not pending:
        return jsonify(ok=True, no_pending_thread=True)

    verdict = data.get("verdict_line", "VERDICT: — no verdict recorded")
    qa_count = data.get("qa_count", 0)

    bolt_app.client.chat_postMessage(
        channel=pending["channel"],
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn",
                "text": f"✅ *Meeting closed* — `{name}`"}},
            {"type": "section", "text": {"type": "mrkdwn",
                "text": f"*{verdict}*\n_Q&A exchanges:_ {qa_count}"}},
            {"type": "actions", "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "🔊 Replay"},
                 "url": f"{SEMECLAW_URL}/api/meeting/audio?name={name}"},
                {"type": "button", "text": {"type": "plain_text", "text": "📄 Transcript"},
                 "url": f"{SEMECLAW_URL}/api/meeting/html?name={name}"},
            ]},
        ],
    )
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# Slack request router
# ---------------------------------------------------------------------------
@flask_app.route("/slack/command", methods=["POST"])
@flask_app.route("/slack/events", methods=["POST"])
def slack_router():
    return handler.handle(request)


if __name__ == "__main__":
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "3000")))
