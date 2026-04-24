"""Tests for structured JSON logging + request-id propagation."""

from __future__ import annotations

import importlib
import io
import json
import logging

from fastapi.testclient import TestClient

from war_room.utils.logging_config import (
    current_request_id,
    new_request_id,
    set_request_id,
    setup_logging,
)


def test_new_request_id_is_short_hex() -> None:
    rid = new_request_id()
    assert len(rid) == 12
    int(rid, 16)  # must parse as hex


def test_contextvar_round_trip() -> None:
    assert current_request_id() == ""
    set_request_id("abc123")
    assert current_request_id() == "abc123"
    set_request_id("")
    assert current_request_id() == ""


def test_json_formatter(monkeypatch) -> None:
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    setup_logging(force_json=True)
    # reinstall with our buffer handler
    from war_room.utils.logging_config import _JsonFormatter, _RequestIdFilter

    handler.setFormatter(_JsonFormatter())
    handler.addFilter(_RequestIdFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    set_request_id("rid-test")
    logging.getLogger("war_room.test").warning("hello %s", "world")
    set_request_id("")
    lines = [l for l in buf.getvalue().splitlines() if l.strip()]
    payload = json.loads(lines[-1])
    assert payload["level"] == "WARNING"
    assert payload["message"] == "hello world"
    assert payload["request_id"] == "rid-test"
    assert "ts" in payload


def test_request_id_middleware_honours_inbound_header(monkeypatch):
    monkeypatch.setenv("SEMECLAW_LOG_JSON", "0")
    import war_room.dashboard.server as srv

    importlib.reload(srv)
    client = TestClient(srv.app)
    r = client.get("/api/agent/manifest", headers={"X-Request-ID": "caller-123"})
    assert r.headers.get("x-request-id") == "caller-123"


def test_request_id_middleware_generates_when_missing(monkeypatch):
    monkeypatch.setenv("SEMECLAW_LOG_JSON", "0")
    import war_room.dashboard.server as srv

    importlib.reload(srv)
    client = TestClient(srv.app)
    r = client.get("/api/agent/manifest")
    rid = r.headers.get("x-request-id")
    assert rid and len(rid) == 12
