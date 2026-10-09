"""XMPP transport layer.

The factory returns the transport matching ``xmpp.mode`` while the runtime only
ever sees :class:`~agent.xmpp.events.XMPPTransport`.
"""

from __future__ import annotations

from agent.config.models import AppConfig
from agent.errors import ConfigError
from agent.xmpp.base import BaseTransport
from agent.xmpp.client import C2STransport
from agent.xmpp.component import ComponentTransport
from agent.xmpp.events import (
    MessageEvent,
    MUCMessageEvent,
    PresenceEvent,
    XMPPTransport,
)


def build_transport(config: AppConfig) -> BaseTransport:
    """Construct the transport implied by ``config.xmpp.mode``."""
    if config.xmpp.mode == "c2s":
        return C2STransport(config)
    if config.xmpp.mode == "component":
        return ComponentTransport(config)
    raise ConfigError(f"Unsupported xmpp.mode: {config.xmpp.mode!r}")


__all__ = [
    "BaseTransport",
    "C2STransport",
    "ComponentTransport",
    "MessageEvent",
    "MUCMessageEvent",
    "PresenceEvent",
    "XMPPTransport",
    "build_transport",
]
