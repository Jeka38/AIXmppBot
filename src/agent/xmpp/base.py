"""Shared behaviour for C2S and component transports.

Handles plugin registration, event decoding into transport-neutral dataclasses,
and reconnection bookkeeping. Concrete subclasses create the Slixmpp stream.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, Literal

import slixmpp
from slixmpp.xmlstream import JID

from agent.config.models import AppConfig, RoomConfig
from agent.errors import TimeoutError_, XMPPError
from agent.xmpp.events import MessageEvent, MUCMessageEvent, PresenceEvent
from agent.xmpp.markup import MarkupResult, MarkupSpan, markdown_to_markup
from agent.xmpp.xhtml import render_xhtml_im

MessageType = Literal["chat", "error", "groupchat", "headline", "normal"]

_MUC_USER_NS = "http://jabber.org/protocol/muc#user"
_OOB_NS = "jabber:x:oob"

log = logging.getLogger(__name__)

MessageCallback = Callable[[MessageEvent], Awaitable[None]]
MUCCallback = Callable[[RoomConfig, MUCMessageEvent], Awaitable[None]]
PresenceCallback = Callable[[PresenceEvent], Awaitable[None]]

#: Plugins registered on both transports (SPEC §5, §6, §7, §25, §41-43).
BASE_PLUGINS = (
    "xep_0030",  # Service Discovery
    "xep_0066",  # Out-of-Band Data (attachments)
    "xep_0071",  # XHTML-IM (dependency of xep_0394)
    "xep_0085",  # Chat State Notifications
    "xep_0115",  # Entity Capabilities
    "xep_0199",  # Ping / keepalive
    "xep_0394",  # Message Markup
)


class BaseTransport:
    """Wraps a Slixmpp stream and exposes the :class:`XMPPTransport` interface."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.xmpp: slixmpp.basexmpp.BaseXMPP | None = None
        self._rooms: dict[str, RoomConfig] = {room.jid.lower(): room for room in config.rooms}
        self._connected = asyncio.Event()
        self._disconnected = asyncio.Event()
        self._closing = False

        self.on_message: MessageCallback | None = None
        self.on_muc_message: MUCCallback | None = None
        self.on_presence: PresenceCallback | None = None
        self.on_connected: Callable[[], Awaitable[None]] | None = None
        self.on_disconnected: Callable[[], Awaitable[None]] | None = None
        #: Returns whether to approve a roster subscription from a JID.
        self.subscription_decider: Callable[[str], bool] | None = None

    # -- lifecycle hooks (subclass responsibility) -----------------------

    def _build_stream(self) -> slixmpp.basexmpp.BaseXMPP:
        raise NotImplementedError

    def _connect_stream(self, xmpp: slixmpp.basexmpp.BaseXMPP) -> None:
        """Initiate the underlying connection (subclasses may pass a host/port)."""
        xmpp.connect()

    # -- public API ------------------------------------------------------

    async def connect(self) -> None:
        xmpp = self._build_stream()
        self.xmpp = xmpp
        self._register_plugins(xmpp)
        self._register_handlers(xmpp)
        self._connect_stream(xmpp)
        try:
            await asyncio.wait_for(
                self._connected.wait(), timeout=self.config.xmpp.connection_timeout
            )
        except TimeoutError as exc:
            raise TimeoutError_("Timed out waiting for XMPP connection/authentication") from exc

    def _register_plugins(self, xmpp: slixmpp.basexmpp.BaseXMPP) -> None:
        for plugin in BASE_PLUGINS:
            try:
                xmpp.register_plugin(plugin)
            except Exception:  # pragma: no cover - plugin availability varies
                log.debug("Plugin %s unavailable", plugin)

    def _register_handlers(self, xmpp: slixmpp.basexmpp.BaseXMPP) -> None:
        xmpp.add_event_handler("session_start", self._on_session_start)
        xmpp.add_event_handler("disconnected", self._on_disconnected)
        xmpp.add_event_handler("message", self._on_message)
        xmpp.add_event_handler("presence", self._on_presence)
        # Roster subscription requests: accept only allowlisted senders.
        xmpp.add_event_handler("roster_subscription_request", self._on_subscription_request)

    def _on_subscription_request(self, presence: Any) -> None:
        """Decide whether to approve an incoming roster subscription.

        The application wires :attr:`subscription_decider` to the authorizer.
        When unset, nothing is auto-approved (fail closed).
        """
        if self.subscription_decider is None:
            return
        try:
            from_jid = str(presence["from"].bare)
            item = self.xmpp.roster[presence["to"]][presence["from"]]  # type: ignore[union-attr]
            if self.subscription_decider(from_jid):
                log.info("Approving roster subscription from %s", from_jid)
                item.authorize()
            else:
                log.info("Rejecting roster subscription from %s", from_jid)
                item.unauthorize()
        except Exception:  # pragma: no cover - roster internals vary
            log.exception("Failed to handle subscription request")

    def register_muc(self, xmpp: slixmpp.basexmpp.BaseXMPP) -> None:
        """Register MUC support (C2S only)."""
        xmpp.register_plugin("xep_0045")
        xmpp.add_event_handler("groupchat_message", self._on_groupchat_message)
        xmpp.add_event_handler("groupchat_presence", self._on_groupchat_presence)

    # -- room registration (config + runtime) ----------------------------

    def register_room(self, room: RoomConfig) -> None:
        """Track a room so its messages are routed to the runtime.

        Config-defined rooms are registered at construction; rooms added at
        runtime (e.g. via ``/join``) go through the same map.
        """
        self._rooms[room.jid.lower()] = room

    def unregister_room(self, room_jid: str) -> None:
        self._rooms.pop(room_jid.lower(), None)

    def room_for(self, room_jid: str) -> RoomConfig | None:
        return self._rooms.get(room_jid.lower())

    def rooms(self) -> list[RoomConfig]:
        return list(self._rooms.values())

    def is_connected(self) -> bool:
        return self._connected.is_set()

    # -- Slixmpp event handlers ------------------------------------------

    async def _on_session_start(self, event: Any = None) -> None:
        self._connected.set()
        self._disconnected.clear()
        await self._after_connected()
        if self.on_connected is not None:
            await self.on_connected()

    async def _after_connected(self) -> None:
        """Subclass hook: send presence / rejoin rooms."""

    def _on_disconnected(self, event: Any = None) -> None:
        log.warning("XMPP disconnected: %s", event)
        self._connected.clear()
        self._disconnected.set()

    async def _on_message(self, msg: Any) -> None:
        if msg["type"] not in ("chat", "normal"):
            return
        body = (msg["body"] or "").strip()
        if not body:
            return
        event = MessageEvent(
            sender_jid=str(msg["from"].bare),
            text=body,
            message_id=msg["id"] or None,
            attachments=_extract_oob(msg),
        )
        if self.on_message is not None:
            await self.on_message(event)

    async def _on_groupchat_message(self, msg: Any) -> None:
        room_jid = str(msg["from"].bare).lower()
        room = self._rooms.get(room_jid)
        if room is None:
            return
        # Only live groupchat messages; ignore errors/private MUC messages.
        if (msg["type"] or "") not in ("groupchat", ""):
            return
        body = (msg["body"] or "").strip()
        if not body:
            return
        nick = str(_muc_nick(msg) or "")
        if nick and nick == room.nick:
            # Never process our own messages (avoids reply loops).
            return
        real_jid = _presence_jid(msg)
        sender_jid = real_jid or f"{room_jid}/{nick}"
        anonymous = real_jid is None
        affiliation = self._occupant_affiliation(room_jid, nick, msg)
        mentioned = _is_mention(body, room)
        event = MUCMessageEvent(
            room_jid=room_jid,
            sender_jid=sender_jid,
            nick=nick,
            text=strip_mention(body, room),
            mentioned=mentioned,
            message_id=msg["id"] or None,
            attachments=_extract_oob(msg),
            anonymous=anonymous,
            affiliation=affiliation,
        )
        if self.on_muc_message is not None:
            await self.on_muc_message(room, event)

    async def _on_groupchat_presence(self, pres: Any) -> None:
        # Occupant tracking is maintained by xep_0045; surface changes.
        if self.on_presence is None:
            return
        room_jid = str(pres["from"].bare).lower()
        room = self._rooms.get(room_jid)
        if room is None:
            return
        nick = str(pres["from"].resource)
        jid = self._resolve_occupant_jid(room_jid, nick, pres)
        available = pres["type"] != "unavailable"
        await self.on_presence(
            PresenceEvent(jid=jid, available=available, status=pres["status"] or "")
        )

    async def _on_presence(self, pres: Any) -> None:
        if self.on_presence is None:
            return
        await self.on_presence(
            PresenceEvent(
                jid=str(pres["from"].bare),
                available=pres["type"] != "unavailable",
                status=pres["status"] or "",
                show=pres["show"] or "",
            )
        )

    def _resolve_occupant_jid(self, room_jid: str, nick: str, stanza: Any) -> str:
        """Best-effort mapping of a MUC nick to a real JID (may be anonymous)."""
        presence_hash = _presence_jid(stanza)
        if presence_hash:
            return presence_hash
        xmpp = self.xmpp
        if xmpp is not None:
            try:
                jid = xmpp.plugin["xep_0045"].get_jid_property(JID(room_jid), nick, "jid")
                if jid:
                    return str(jid)
            except Exception:  # pragma: no cover - anonymous rooms have no JID
                pass
        return f"{room_jid}/{nick}"

    def _occupant_affiliation(self, room_jid: str, nick: str, stanza: Any) -> str:
        """Return the occupant's affiliation (owner/admin/member/none).

        Affiliation is included in the ``muc#user/item`` element of every
        presence and is visible even in anonymous rooms where the real JID is
        hidden. The value is also cached from the xep_0045 roster.
        """
        item = _muc_item(stanza)
        if item is not None and item.get("affiliation"):
            return str(item.get("affiliation")).lower()
        xmpp = self.xmpp
        if xmpp is not None:
            try:
                value = xmpp.plugin["xep_0045"].get_jid_property(JID(room_jid), nick, "affiliation")
                if value:
                    return str(value).lower()
            except Exception:  # pragma: no cover - unknown occupant
                pass
        return ""

    # -- outbound messaging ----------------------------------------------

    async def send_message(
        self,
        to_jid: str,
        body: str,
        *,
        message_type: MessageType = "chat",
        composing: bool = False,
    ) -> None:
        if self.xmpp is None:
            raise XMPPError("Transport is not connected")
        self._send_message_stanza(to_jid, body, message_type)
        if composing:
            self._send_chat_state(to_jid, "active")

    async def send_chat_state(self, to_jid: str, state: str) -> None:
        if self.xmpp is None:
            return
        self._send_chat_state(to_jid, state)

    def _send_chat_state(self, to_jid: str, state: str) -> None:
        """Attach an XEP-0085 chat state to an empty message (no body)."""
        assert self.xmpp is not None
        message = self.xmpp.make_message(mto=JID(to_jid), mtype="chat")
        message["chat_state"] = state
        message.send()

    def _send_message_stanza(self, to_jid: str, body: str, message_type: MessageType) -> None:
        assert self.xmpp is not None
        markup = markdown_to_markup(body)
        if not markup.has_markup:
            self.xmpp.send_message(mto=JID(to_jid), mbody=markup.body, mtype=message_type)
            return
        message = self.xmpp.make_message(mto=JID(to_jid), mbody=markup.body, mtype=message_type)
        self._apply_markup(message, markup)
        self._apply_xhtml_im(message, body)
        message.send()

    def _apply_xhtml_im(self, message: Any, body: str) -> None:
        """Attach XHTML-IM (XEP-0071) for clients that do not support XEP-0394."""
        try:
            message["html"]["body"] = render_xhtml_im(body)
        except Exception:  # noqa: BLE001 - formatting must never block a reply
            log.debug("Failed to build XHTML-IM, sending markup/plain only")

    def _apply_markup(self, message: Any, markup: MarkupResult) -> None:
        """Attach XEP-0394 elements to a message stanza."""
        from slixmpp.plugins.xep_0394.stanza import (
            BlockCode,
            BlockQuote,
            Li,
            List,
            Span,
        )

        container = message["markup"]
        for element in markup.elements:
            if isinstance(element, MarkupSpan):
                span = Span()
                span["start"] = element.start
                span["end"] = element.end
                # Slixmpp 1.10 only knows emphasis/code/deleted; add <strong/>
                # (XEP-0394 v0.3) manually when requested.
                supported = [t for t in element.types if t != "strong"]
                span["types"] = supported
                if "strong" in element.types:
                    from slixmpp.xmlstream import ET

                    span.xml.append(ET.Element("{urn:xmpp:markup:0}strong"))
                container.append(span)
            elif element.kind == "bcode":
                code = BlockCode()
                code["start"] = element.start
                code["end"] = element.end
                if element.language:
                    code["language"] = element.language
                container.append(code)
            elif element.kind == "bquote":
                quote = BlockQuote()
                quote["start"] = element.start
                quote["end"] = element.end
                container.append(quote)
            elif element.kind == "list":
                listing = List()
                listing["start"] = element.start
                listing["end"] = element.end
                # Slixmpp 1.10 predates the `ordered` attribute of XEP-0394 v0.3.
                if element.ordered:
                    listing.xml.set("ordered", "true")
                for item_start in element.items:
                    li = Li()
                    li["start"] = item_start
                    listing.append(li)
                container.append(listing)

    async def send_presence(
        self, *, show: str = "", status: str = "", to_jid: str | None = None
    ) -> None:
        if self.xmpp is None:
            return
        self.xmpp.send_presence(
            pfrom=JID(to_jid) if to_jid else None,
            pshow=show or None,
            pstatus=status or None,
        )

    async def join_muc(self, room_jid: str, nick: str, *, password: str = "") -> None:
        if self.xmpp is None:
            return
        self.xmpp.plugin["xep_0045"].join_muc(JID(room_jid), nick, password=password)

    async def leave_muc(self, room_jid: str, nick: str, *, status: str = "") -> None:
        if self.xmpp is None:
            return
        self.xmpp.plugin["xep_0045"].leave_muc(JID(room_jid), nick, msg=status)

    async def send_muc_message(self, room_jid: str, body: str) -> None:
        if self.xmpp is None:
            raise XMPPError("Transport is not connected")
        self._send_message_stanza(room_jid, body, "groupchat")

    async def disconnect(self) -> None:
        self._closing = True
        if self.xmpp is None:
            return
        if self.config.xmpp.mode == "c2s":
            for room in self._rooms.values():
                try:
                    await self.leave_muc(room.jid, room.nick, status="Agent shutting down")
                except Exception:  # pragma: no cover - best effort
                    log.debug("Failed to leave %s", room.jid)
        self.xmpp.disconnect()
        self._closing = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_oob(msg: Any) -> list[str]:
    """Return URLs from XEP-0066 OOB data (SPEC §25).

    Parses the ``jabber:x:oob`` element directly from the stanza XML so it works
    whether or not the xep_0066 plugin is loaded.
    """
    urls: list[str] = []
    for oob in msg.xml.findall(f"{{{_OOB_NS}}}x"):
        for url in oob.findall(f"{{{_OOB_NS}}}url"):
            if url.text and url.text.strip():
                urls.append(url.text.strip())
    # Some senders attach <url> directly under the OOB namespace without <x>.
    for url in msg.xml.findall(f"{{{_OOB_NS}}}url"):
        if url.text and url.text.strip():
            urls.append(url.text.strip())
    return urls


def _muc_item(stanza: Any) -> Any:
    """Find the ``muc#user/item`` element, whether on the stanza or its ``x``."""
    direct = stanza.xml.find(f"{{{_MUC_USER_NS}}}item")
    if direct is not None:
        return direct
    return stanza.xml.find(f"{{{_MUC_USER_NS}}}x/{{{_MUC_USER_NS}}}item")


def _presence_jid(stanza: Any) -> str | None:
    """Extract the real JID from a MUC presence's ``muc#user/item`` element."""
    item = _muc_item(stanza)
    if item is None:
        return None
    jid = item.get("jid")
    return str(jid) if jid else None


def _muc_nick(stanza: Any) -> str | None:
    """Return the MUC nickname from the item element or the sender resource."""
    item = _muc_item(stanza)
    nick = item.get("nick") if item is not None else None
    if nick:
        return str(nick)
    resource = str(stanza["from"].resource)
    return resource or None


def _is_mention(body: str, room: RoomConfig) -> bool:
    """Whether *body* addresses the bot.

    Matching is case-insensitive and word-boundary aware so a short nick like
    ``AI`` does not match inside ``said`` or ``email``. ``@nick`` always counts.
    """
    lowered = body.lower()
    candidates = [room.nick, *room.aliases]
    for candidate in candidates:
        nick = candidate.lower()
        if not nick:
            continue
        if f"@{nick}" in lowered:
            return True
        if re.search(rf"(?<![\w@]){re.escape(nick)}(?![\w])", lowered):
            return True
    return False


def strip_mention(body: str, room: RoomConfig) -> str:
    """Remove a leading mention of the bot from a room message.

    Handles ``AI: text``, ``@AI text``, ``AI, text`` and the bare nick. Only a
    *leading* mention is stripped; mentions inside the sentence are kept. This
    lets ``AI: /persona list`` be recognised as the ``/persona`` command and
    keeps the bot's name out of the text sent to the model.
    """
    text = body.lstrip()
    lowered = text.lower()
    candidates: list[str] = []
    for candidate in (f"@{room.nick}", room.nick, *room.aliases, *(f"@{a}" for a in room.aliases)):
        c = candidate.lower()
        if c and c not in candidates:
            candidates.append(c)
    # Longest first so "ai-agent" wins over "ai".
    for candidate in sorted(candidates, key=len, reverse=True):
        if not lowered.startswith(candidate):
            continue
        remainder = text[len(candidate) :]
        if remainder and remainder[0] not in ":,;-—– \t":
            continue  # e.g. "air" must not be read as the nick "ai"
        stripped = remainder.lstrip(":,;-—– \t").strip()
        return stripped
    return body.strip()
