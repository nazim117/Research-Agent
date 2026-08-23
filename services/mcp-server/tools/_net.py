"""Shared SSRF guard for tools that fetch arbitrary agent-supplied URLs
(web_fetch, http_request) — NOT used by web_search, whose outbound calls go
to fixed, developer-configured backends (SearXNG/Brave/DuckDuckGo), not
arbitrary URLs.

Resolves the hostname and rejects loopback/private/link-local/reserved/
multicast addresses (covers cloud metadata endpoints like 169.254.169.254,
which is link-local). This stops the common case — an agent given, or
tricked via a prompt-injected page into fetching, a URL that points at
internal infrastructure — but is not a complete defense against DNS
rebinding (resolving to a public IP at check time, then a private IP at
connect time). Full protection would mean pinning the connection to the
checked IP, which httpx doesn't support without a custom transport; out of
scope here.

Leading underscore: server.py's _register_all skips modules starting with
"_", so this is a private helper other tools/* modules can import without
it becoming a registered MCP tool itself.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

_ALLOWED_SCHEMES = {"http", "https"}


class UnsafeURLError(ValueError):
    """A ValueError subclass so it's surfaced the same way existing tools
    already report validation failures to FastMCP (e.g. files.py's
    path-traversal errors)."""


def _resolve(hostname: str) -> set[str]:
    """Thin wrapper around socket.getaddrinfo so tests can monkeypatch this
    one call instead of doing real DNS lookups. A literal IP address (e.g.
    "169.254.169.254") resolves here without touching the network."""
    return {info[4][0] for info in socket.getaddrinfo(hostname, None)}


def assert_safe_url(url: str) -> None:
    """Raise UnsafeURLError if `url` isn't a plain http(s) URL pointing at a
    public address."""
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeURLError(f"unsupported scheme in {url!r} — only http/https are allowed")
    if not parsed.hostname:
        raise UnsafeURLError(f"invalid URL {url!r}: no hostname")

    try:
        addrs = _resolve(parsed.hostname)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"could not resolve host {parsed.hostname!r}: {exc}") from exc

    for addr in addrs:
        ip = ipaddress.ip_address(addr)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise UnsafeURLError(
                f"refusing to fetch {url!r}: host {parsed.hostname!r} resolves to "
                f"non-public address {addr}"
            )


__all__ = ["UnsafeURLError", "assert_safe_url"]
