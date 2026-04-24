"""Tests for the Range-aware audio response helper."""

from __future__ import annotations

import importlib
import os

import pytest


@pytest.fixture
def server():
    """Import server with minimal env so its helper is accessible."""
    os.environ.setdefault("SEMECLAW_API_KEY", "")
    # Import via a dotted name so FastAPI init doesn't fail on missing config
    import war_room.dashboard.server as mod  # noqa: WPS433

    importlib.reload(mod)
    return mod


class _FakeRequest:
    def __init__(self, range_header: str = "") -> None:
        self.headers: dict[str, str] = {}
        if range_header:
            self.headers["range"] = range_header


def test_no_range_returns_full_payload(server) -> None:
    data = b"0123456789"
    resp = server._range_audio_response(data, _FakeRequest())
    assert resp.status_code == 200
    assert resp.body == data
    assert resp.headers.get("accept-ranges") == "bytes"


def test_single_range_returns_partial(server) -> None:
    data = b"0123456789"
    resp = server._range_audio_response(data, _FakeRequest("bytes=2-5"))
    assert resp.status_code == 206
    assert resp.body == b"2345"
    assert resp.headers.get("content-range") == f"bytes 2-5/{len(data)}"
    assert resp.headers.get("content-length") == "4"


def test_open_ended_range(server) -> None:
    data = b"0123456789"
    resp = server._range_audio_response(data, _FakeRequest("bytes=5-"))
    assert resp.status_code == 206
    assert resp.body == b"56789"


def test_malformed_range_falls_back_to_full(server) -> None:
    data = b"abcdef"
    resp = server._range_audio_response(data, _FakeRequest("bytes=garbage"))
    assert resp.status_code == 200
    assert resp.body == data


def test_out_of_bounds_range_falls_back_to_full(server) -> None:
    data = b"abcdef"
    resp = server._range_audio_response(data, _FakeRequest("bytes=10-20"))
    assert resp.status_code == 200
    assert resp.body == data


def test_multipart_range_declined(server) -> None:
    data = b"0123456789"
    # Multipart — return full body
    resp = server._range_audio_response(data, _FakeRequest("bytes=0-1,3-4"))
    assert resp.status_code == 200
    assert resp.body == data
