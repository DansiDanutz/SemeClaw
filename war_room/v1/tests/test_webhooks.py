"""Tests for the webhook outbox."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json

import pytest


@pytest.fixture()
def v1_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("SEMECLAW_V1_DATA_DIR", str(tmp_path))
    yield tmp_path


@pytest.mark.asyncio
async def test_subscribe_validates_url(v1_data_dir):
    from war_room.v1 import webhooks

    with pytest.raises(ValueError):
        await webhooks.subscribe(tenant_id="acme", url="ftp://x", events=["*"])
    with pytest.raises(ValueError):
        await webhooks.subscribe(tenant_id="acme", url="https://x", events=[])


@pytest.mark.asyncio
async def test_subscribe_and_list(v1_data_dir):
    from war_room.v1 import webhooks

    sub = await webhooks.subscribe(tenant_id="acme", url="https://acme.test/hook", events=["meeting.finalized"])
    assert sub.id.startswith("wsub_")
    assert sub.secret.startswith("whsec_")

    rows = await webhooks.list_subscriptions(tenant_id="acme")
    assert len(rows) == 1
    assert rows[0].url == "https://acme.test/hook"


@pytest.mark.asyncio
async def test_dispatch_skips_non_matching_events(v1_data_dir):
    from war_room.v1 import webhooks

    await webhooks.subscribe(tenant_id="acme", url="https://acme.test/hook", events=["meeting.finalized"])
    n = await webhooks.dispatch("intervention.recorded", {"x": 1}, tenant_id="acme")
    assert n == 0


@pytest.mark.asyncio
async def test_dispatch_falls_back_to_dlq_on_unreachable_url(v1_data_dir, tmp_path):
    from war_room.v1 import webhooks

    await webhooks.subscribe(
        tenant_id="acme",
        url="http://127.0.0.1:1/dead",  # connection refused
        events=["*"],
    )
    n = await webhooks.dispatch("meeting.finalized", {"meeting_id": "m1"}, tenant_id="acme")
    assert n == 1

    # Wait for the async task to land in the DLQ.
    for _ in range(30):
        await asyncio.sleep(0.05)
        dlq = tmp_path / "webhooks_dlq.jsonl"
        if dlq.exists() and dlq.read_text().strip():
            break
    else:  # pragma: no cover
        pytest.fail("webhook never landed in DLQ")

    rows = [json.loads(line) for line in (tmp_path / "webhooks_dlq.jsonl").read_text().splitlines() if line.strip()]
    assert len(rows) >= 1
    assert rows[0]["kind"] == "webhook_delivery"
    assert rows[0]["url"].endswith("/dead")
    assert "X-SemeClaw-Signature" in rows[0]["headers"]


def test_hmac_signature_is_recomputable():
    from war_room.v1 import webhooks

    body = b'{"event":"x"}'
    ts = 1700000000
    sig = webhooks._sign("whsec_test", body, ts)
    expected = hmac.new(b"whsec_test", f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    assert sig == expected
