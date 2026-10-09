"""C2S transport: connects an ordinary XMPP account (SPEC §2)."""

from __future__ import annotations

import logging
import ssl

import slixmpp

from agent.config.models import AppConfig
from agent.xmpp.base import BaseTransport

log = logging.getLogger(__name__)


class C2STransport(BaseTransport):
    """Client-to-server transport with STARTTLS, SASL and MUC support."""

    def __init__(self, config: AppConfig) -> None:
        super().__init__(config)
        self._bound_jid: str = ""

    def _build_stream(self) -> slixmpp.ClientXMPP:
        xmpp = slixmpp.ClientXMPP(
            self.config.xmpp.jid,
            self.config.xmpp.password,
            sasl_mech=None,
        )
        xmpp.enable_starttls = self.config.xmpp.starttls
        xmpp.enable_direct_tls = False
        if self._use_custom_address():
            xmpp.default_domain = self._jid_domain()
        if not self.config.xmpp.verify_tls:
            # Explicit, config-gated relaxation for self-signed dev servers.
            context = xmpp.get_ssl_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            xmpp.ssl_context = context
        return xmpp

    def _use_custom_address(self) -> bool:
        return bool(self.config.xmpp.host and self.config.xmpp.port)

    def _jid_domain(self) -> str:
        jid = self.config.xmpp.jid
        return jid.split("@", 1)[1].split("/", 1)[0] if "@" in jid else jid

    def _register_handlers(self, xmpp: slixmpp.basexmpp.BaseXMPP) -> None:
        super()._register_handlers(xmpp)
        self.register_muc(xmpp)

    async def _after_connected(self) -> None:
        assert self.xmpp is not None
        self._bound_jid = str(self.xmpp.boundjid.bare)
        self.xmpp.send_presence(pstatus="Online")
        for room in self._rooms.values():
            await self.join_muc(room.jid, room.nick, password=room.password)

    def _connect_stream(self, xmpp: slixmpp.basexmpp.BaseXMPP) -> None:
        if self._use_custom_address():
            xmpp.connect(self.config.xmpp.host, self.config.xmpp.port)
        else:
            xmpp.connect()
