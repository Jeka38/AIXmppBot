"""Skill capability model.

Each skill declares which capabilities it needs (SPEC §31). The runtime checks
the declaration against the granted capability set before executing anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

_NETWORK_DEFAULT_PORTS = {"http": "80", "https": "443"}


class Capability(StrEnum):
    NETWORK = "network"
    FILESYSTEM = "filesystem"
    CREDENTIAL = "credential"
    SCHEDULE = "schedule"
    STATE = "state"
    XMPP = "xmpp"


def normalize_network_target(target: str) -> str:
    """Canonicalise a network target to ``host:port``.

    Accepts ``host``, ``host:port``, ``http://host/path`` and
    ``https://host/path``. Bare hosts default to port 443 (https). IPv6
    literals may be given in brackets.
    """
    value = target.strip().lower()
    if not value:
        return value
    if "://" in value:
        from urllib.parse import urlsplit

        parts = urlsplit(value)
        host = parts.hostname or ""
        port = parts.port or (443 if parts.scheme == "https" else 80)
        return f"{host}:{port}"
    if value.startswith("["):
        host, _, rest = value.partition("]")
        host = host + "]"
        if rest.startswith(":"):
            return f"{host}:{rest[1:]}"
        return f"{host}:443"
    if value.count(":") == 1:
        host, _, raw_port = value.partition(":")
        return f"{host}:{raw_port or '443'}"
    if ":" in value:  # bare IPv6 literal
        return f"[{value}]:443"
    return f"{value}:443"


def _normalise_target(capability: Capability, target: str) -> str:
    if capability is Capability.NETWORK:
        return normalize_network_target(target)
    return target.strip().lower()


@dataclass(frozen=True)
class CapabilityGrant:
    """A concrete permission: a capability plus optional scoped targets.

    ``targets`` is empty for capabilities that carry no scope (e.g. ``state``),
    a list of ``host:port`` entries for ``network``, or credential names for
    ``credential``.
    """

    capability: Capability
    targets: frozenset[str] = frozenset()

    @classmethod
    def parse(
        cls,
        capability: Capability | str,
        targets: list[str] | None = None,
    ) -> CapabilityGrant:
        cap = capability if isinstance(capability, Capability) else Capability(capability)
        normalised = frozenset(
            _normalise_target(cap, target) for target in (targets or []) if target.strip()
        )
        return cls(capability=cap, targets=normalised)

    def permits(self, requested: CapabilityGrant) -> bool:
        """Return whether this grant covers *requested*.

        A grant with no targets permits any target of that capability. A grant
        with targets requires every requested target to be present.
        """
        if self.capability != requested.capability:
            return False
        if not self.targets:
            return True
        if not requested.targets:
            return False
        return requested.targets.issubset(self.targets)


@dataclass
class CapabilitySet:
    """The capabilities granted to a single skill invocation."""

    grants: list[CapabilityGrant] = field(default_factory=list)

    def allows(self, requested: CapabilityGrant) -> bool:
        return any(grant.permits(requested) for grant in self.grants)

    def check(self, requested: CapabilityGrant) -> None:
        from agent.errors import AuthorizationError

        if not self.allows(requested):
            targets = ",".join(sorted(requested.targets)) or "*"
            raise AuthorizationError(
                f"Skill lacks capability {requested.capability.value} ({targets})"
            )

    @classmethod
    def from_mapping(cls, mapping: dict[str, list[str]]) -> CapabilitySet:
        return cls(
            grants=[
                CapabilityGrant.parse(capability, targets)
                for capability, targets in mapping.items()
            ]
        )
