"""External callbacks must reject requests when signature secrets are absent."""

from unittest.mock import patch

from starlette.testclient import TestClient

from war_room.dashboard.server import app

# Patch only TELEGRAM_WEBHOOK_SECRET. Patching ``os.environ.get`` on the
# route module hijacks env lookups process-wide (``routes.telegram.os`` is
# the shared ``os`` module) — it once redirected the v1 storage data dir
# into the repository root mid-test.


def test_telegram_webhook_requires_configured_secret() -> None:
    with TestClient(app) as client:
        with patch.dict("os.environ", {"TELEGRAM_WEBHOOK_SECRET": ""}):
            response = client.post("/api/telegram/webhook", json={})
    assert response.status_code == 503


def test_telegram_webhook_rejects_wrong_secret() -> None:
    with TestClient(app) as client:
        with patch.dict("os.environ", {"TELEGRAM_WEBHOOK_SECRET": "configured-secret"}):
            response = client.post(
                "/api/telegram/webhook",
                json={},
                headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
            )
    assert response.status_code == 401
