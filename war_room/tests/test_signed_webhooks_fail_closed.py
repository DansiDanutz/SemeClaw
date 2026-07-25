"""External callbacks must reject requests when signature secrets are absent."""

from unittest.mock import patch

from starlette.testclient import TestClient

from war_room.dashboard.server import app


def test_telegram_webhook_requires_configured_secret() -> None:
    with TestClient(app) as client, patch.dict("os.environ", {}, clear=False):
        with patch("war_room.dashboard.routes.telegram.os.environ.get", return_value=""):
            response = client.post("/api/telegram/webhook", json={})
    assert response.status_code == 503


def test_telegram_webhook_rejects_wrong_secret() -> None:
    with TestClient(app) as client:
        with patch("war_room.dashboard.routes.telegram.os.environ.get", return_value="configured-secret"):
            response = client.post(
                "/api/telegram/webhook",
                json={},
                headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
            )
    assert response.status_code == 401
