import pytest

from war_room.security.outbound import validate_public_https_url


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/hook",
        "https://127.0.0.1/hook",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/hook",
        "https://user:password@example.com/hook",
    ],
)
async def test_webhook_destinations_reject_unsafe_targets(url):
    with pytest.raises(ValueError):
        await validate_public_https_url(url)


@pytest.mark.asyncio
async def test_webhook_destination_accepts_public_https_literal():
    assert await validate_public_https_url("https://8.8.8.8/hook") == "https://8.8.8.8/hook"
