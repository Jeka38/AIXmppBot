"""Network access guard for skills.

Every outbound HTTP call a skill makes must first pass :func:`ensure_network_allowed`,
which checks the target host:port against the caller's granted ``network``
capability. This is checked *in addition to* the runtime's pre-execution
capability check, so a skill cannot silently reach a host it did not declare.

The guard is intentionally only a host/port allowlist. SSRF protection (blocking
private and metadata addresses) is enforced separately in
:mod:`agent.security.ssrf` and must still be applied to any untrusted URL.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from agent.errors import AuthorizationError
from agent.security.capabilities import (
    Capability,
    CapabilityGrant,
    normalize_network_target,
)
from agent.security.credentials import CredentialStore


def network_target_for_url(url: str) -> str:
    """Return the ``host:port`` target for *url*."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if not host:
        raise AuthorizationError(f"Cannot determine host for URL: {url!r}")
    literal = f"[{host}]" if ":" in host else host
    return f"{literal}:{port}"


def ensure_network_allowed(url: str, *, grants: list[CapabilityGrant]) -> None:
    """Raise :class:`AuthorizationError` unless *url*'s host is granted."""
    target = normalize_network_target(network_target_for_url(url))
    request = CapabilityGrant(capability=Capability.NETWORK, targets=frozenset({target}))
    for grant in grants:
        if grant.capability is Capability.NETWORK and not grant.targets:
            return  # unrestricted network grant
    for grant in grants:
        if grant.permits(request):
            return
    raise AuthorizationError(f"Network host not permitted by capability policy: {target}")


class CredentialGuard:
    """Resolves named credentials only if the caller holds the credential grant."""

    def __init__(self, grants: list[CapabilityGrant], store: CredentialStore) -> None:
        self.grants = grants
        self.store = store

    def get(self, name: str, *, required: bool = True) -> str | None:
        target = name.strip().lower()
        request = CapabilityGrant(capability=Capability.CREDENTIAL, targets=frozenset({target}))
        if not any(grant.permits(request) for grant in self.grants):
            raise AuthorizationError(f"Credential not permitted: {name}")
        value = self.store.get(name)
        if value is None and required:
            from agent.errors import AuthenticationError

            raise AuthenticationError(f"Credential '{name}' is not configured")
        return value
