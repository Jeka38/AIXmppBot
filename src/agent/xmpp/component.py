"""XEP-0114 component transport (SPEC §3).

Exposes the same :class:`~agent.xmpp.events.XMPPTransport` interface as C2S so
the runtime cannot tell the two modes apart.
"""

from __future__ import annotations

import logging

import slixmpp

from agent.xmpp.base import BaseTransport

log = logging.getLogger(__name__)


class ComponentTransport(BaseTransport):
    """Jabber component connection (XEP-0114)."""

    def _build_stream(self) -> slixmpp.ComponentXMPP:
        host = self.config.xmpp.host or None
        port = self.config.xmpp.port or None
        xmpp = slixmpp.ComponentXMPP(  # type: ignore[no-untyped-call]
            self.config.xmpp.component_jid,
            self.config.xmpp.secret,
            host=host,
            port=port,
        )
        return xmpp

    def _register_handlers(self, xmpp: slixmpp.basexmpp.BaseXMPP) -> None:
        super()._register_handlers(xmpp)
        # Components may also service rooms when configured explicitly.
        self.register_muc(xmpp)

    async def _after_connected(self) -> None:
        # Components have no roster/presence session; only join configured rooms.
        for room in self._rooms.values():
            await self.join_muc(room.jid, room.nick, password=room.password)
