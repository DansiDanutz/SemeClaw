"""Tests for the ElevenLabs TTS wrapper and fallback logic."""

# Ensure the dashboard modules are importable
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "dashboard"))

import elevenlabs_tts as el


class FakeResponse:
    def __init__(self, status_code, json_data, content=b"fake-mp3"):
        self.status_code = status_code
        self._json = json_data
        self.content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            from httpx import HTTPStatusError

            raise HTTPStatusError("err", request=MagicMock(), response=self)

    def json(self):
        return self._json


def test_health_no_key():
    with patch.object(el, "ELEVENLABS_API_KEY", ""):
        h = el.health()
    assert h["available"] is False
    assert "not set" in h["reason"]


def test_health_bad_key():
    with patch.object(el, "ELEVENLABS_API_KEY", "bad-key"):
        with patch("httpx.Client") as mock_client:
            inst = MagicMock()
            inst.get.return_value = FakeResponse(401, {})
            mock_client.return_value.__enter__ = lambda s: inst
            mock_client.return_value.__exit__ = lambda *a: None
            h = el.health()
    assert h["available"] is False


def test_health_ok():
    with patch.object(el, "ELEVENLABS_API_KEY", "good-key"):
        with patch("httpx.Client") as mock_client:
            inst = MagicMock()
            inst.get.return_value = FakeResponse(
                200,
                {
                    "tier": "starter",
                    "character_limit": 10000,
                    "character_count": 1234,
                },
            )
            mock_client.return_value.__enter__ = lambda s: inst
            mock_client.return_value.__exit__ = lambda *a: None
            h = el.health()
    assert h["available"] is True
    assert h["tier"] == "starter"


def test_synthesize_no_key_returns_none():
    with patch.object(el, "ELEVENLABS_API_KEY", ""):
        result = el.synthesize("Hello")
    assert result is None


def test_synthesize_non_english_returns_none():
    with patch.object(el, "ELEVENLABS_API_KEY", "key"):
        result = el.synthesize("こんにちは")
    assert result is None


def test_synthesize_hits_api_and_caches(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    with patch.object(el, "ELEVENLABS_API_KEY", "key"):
        with patch("httpx.Client") as mock_client:
            inst = MagicMock()
            inst.post.return_value = FakeResponse(200, {}, content=b"mp3-data")
            mock_client.return_value.__enter__ = lambda s: inst
            mock_client.return_value.__exit__ = lambda *a: None

            # First call should hit API
            path1 = el.synthesize("Hello world", agent="narrator", cache_dir=cache_dir)
            assert path1 is not None
            assert path1.exists()
            assert path1.read_bytes() == b"mp3-data"
            assert inst.post.call_count == 1

            # Second call should be cached
            path2 = el.synthesize("Hello world", agent="narrator", cache_dir=cache_dir)
            assert path2 == path1
            # No additional POST
            assert inst.post.call_count == 1


def test_synthesize_http_error_falls_back():
    with patch.object(el, "ELEVENLABS_API_KEY", "key"):
        with patch("httpx.Client") as mock_client:
            inst = MagicMock()
            inst.post.return_value = FakeResponse(429, {})
            mock_client.return_value.__enter__ = lambda s: inst
            mock_client.return_value.__exit__ = lambda *a: None

            result = el.synthesize("Hello")
            assert result is None


def test_resolve_voice_id_passthrough():
    vid = el._resolve_voice_id("pNInz6obpgDQGcFmaJgB")
    assert vid == "pNInz6obpgDQGcFmaJgB"


def test_resolve_voice_id_by_agent():
    vid = el._resolve_voice_id("en-US-SteffanNeural", agent="research")
    assert vid == el.VOICE_MAP["research"]


def test_resolve_voice_id_by_role():
    vid = el._resolve_voice_id("strategist")
    assert vid == el.VOICE_MAP["strategist"]
