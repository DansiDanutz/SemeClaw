"""Validation for user-configured outbound HTTPS destinations."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpcore
import httpx


def _is_public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return bool(ip.is_global)


@dataclass(frozen=True)
class PinnedHttpsTarget:
    url: str
    address: str


class _PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, address: str):
        self._address = address
        self._delegate = httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        return await self._delegate.connect_tcp(
            self._address, port, timeout, local_address, socket_options
        )

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        return await self._delegate.connect_unix_socket(path, timeout, socket_options)

    async def sleep(self, seconds):
        await self._delegate.sleep(seconds)


async def resolve_public_https_url(url: str) -> PinnedHttpsTarget:
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
    return PinnedHttpsTarget(value, sorted(addresses)[0])


async def validate_public_https_url(url: str) -> str:
    return (await resolve_public_https_url(url)).url


def pinned_httpx_transport(target: PinnedHttpsTarget) -> httpx.AsyncHTTPTransport:
    """Create a transport that connects to the validated address but keeps
    the original hostname for the HTTP Host header and TLS SNI verification."""
    transport = httpx.AsyncHTTPTransport(retries=0, trust_env=False)
    transport._pool._network_backend = _PinnedNetworkBackend(target.address)  # noqa: SLF001
    return transport
