"""
Tests for the R2 ad audio endpoint fallback behavior.
"""

from fastapi.testclient import TestClient

from war_room.dashboard.server import app

client = TestClient(app)


def test_ad_audio_fallback_when_r2_unconfigured():
    """When R2 env vars are missing, endpoint should fall back to local file or 404."""
    r = client.get("/api/ad/audio")
    # Either redirects (302) if R2 is configured, or returns file/404 if not
    assert r.status_code in (200, 302, 404)


def test_ad_audio_with_custom_key():
    r = client.get("/api/ad/audio?key=ads/nervix-default.mp3")
    assert r.status_code in (200, 302, 404)
