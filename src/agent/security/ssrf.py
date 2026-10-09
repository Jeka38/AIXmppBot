"""SSRF protection (SPEC §21, §25).

Any URL supplied by a user or the LLM must pass :func:`validate_public_url`
before it is fetched. The check resolves the hostname and rejects loopback,
private, link-local, reserved and multicast addresses so the agent cannot be
tricked into reaching internal services or cloud metadata endpoints.

Because DNS can change between validation and connection (DNS rebinding), the
caller must re-validate on every redirect hop and, where the HTTP client allows
it, pin the connection to the validated address.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

from agent.errors import SecurityError

DEFAULT_ALLOWED_SCHEMES = frozenset({"http", "https"})

#: Explicitly denied networks beyond the stdlib classifications.
_EXTRA_DENIED = (
    ipaddress.ip_network("169.254.169.254/32"),  # AWS/GCP/Azure metadata
    ipaddress.ip_network("100.100.100.200/32"),  # Alibaba Cloud metadata
    ipaddress.ip_network("fd00:ec2::254/128"),  # AWS IMDSv2 IPv6
)


class SSRFError(SecurityError):
    """Raised when a URL is rejected for safety reasons."""


@dataclass(frozen=True)
class ValidatedURL:
    url: str
    scheme: str
    host: str
    port: int
    addresses: tuple[str, ...]


def is_blocked_ip(ip: str) -> bool:
    """Return ``True`` if *ip* must never be fetched."""
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return True
    if any(address in network for network in _EXTRA_DENIED):
        return True
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def validate_public_url(
    url: str,
    *,
    allowed_schemes: frozenset[str] = DEFAULT_ALLOWED_SCHEMES,
    allow_private: bool = False,
) -> ValidatedURL:
    """Validate *url* and resolve it to a set of public IP addresses.

    Raises :class:`SSRFError` on any violation. ``allow_private`` is an explicit
    operator escape hatch (e.g. for a private wiki) and must never be enabled by
    untrusted input.
    """
    if not url or "://" not in url:
        raise SSRFError(f"Not an absolute URL: {url!r}")

    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in allowed_schemes:
        raise SSRFError(f"Scheme not allowed: {scheme!r}")
    host = (parts.hostname or "").strip().lower()
    if not host:
        raise SSRFError("URL has no host")

    if not allow_private:
        _reject_literal_ip(host)

    port = parts.port or (443 if scheme == "https" else 80)
    addresses = _resolve(host)

    if not allow_private:
        for address in addresses:
            if is_blocked_ip(address):
                raise SSRFError(
                    f"Refusing to fetch {host!r}: resolves to blocked address {address}"
                )
    return ValidatedURL(
        url=url.strip(),
        scheme=scheme,
        host=host,
        port=port,
        addresses=tuple(addresses),
    )


def _reject_literal_ip(host: str) -> None:
    """Reject a host that is itself an IP literal in a blocked range."""
    candidate = host.strip("[]")
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return
    if is_blocked_ip(candidate):
        raise SSRFError(f"Refusing to fetch blocked IP literal {host!r}")


def _resolve(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise SSRFError(f"Could not resolve host {host!r}: {exc}") from exc
    addresses: list[str] = []
    for info in infos:
        address = str(info[4][0])
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise SSRFError(f"Host {host!r} resolved to no addresses")
    return addresses
