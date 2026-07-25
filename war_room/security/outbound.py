"""Validation for user-configured outbound HTTPS destinations."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit


def _is_public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return bool(ip.is_global)


async def validate_public_https_url(url: str) -> str:
    """Return *url* when it resolves only to public addresses.

    Resolution is repeated immediately before every delivery so a hostname
    cannot be registered as public and later silently changed to a local
    control-plane address.
    """
    value = url.strip()
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("webhook URL must use https")
    if parsed.username or parsed.password:
        raise ValueError("webhook URL must not contain credentials")
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise ValueError("webhook URL has an invalid port") from exc

    try:
        results = await asyncio.to_thread(
            socket.getaddrinfo,
            parsed.hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise ValueError("webhook hostname could not be resolved") from exc
    addresses = {item[4][0].split("%", 1)[0] for item in results}
    if not addresses or any(not _is_public_address(address) for address in addresses):
        raise ValueError("webhook hostname must resolve only to public addresses")
    return value
