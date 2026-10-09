"""Transport event dataclasses.

The runtime consumes these; they contain no Slixmpp types so the core stays
transport-independent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class MessageEvent:
    """A direct (1:1) chat message."""

    sender_jid: str
    text: str
    message_id: str | None = None
    attachments: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MUCMessageEvent:
    """A group-chat message."""

    room_jid: str
    sender_jid: str
    nick: str
    text: str
    mentioned: bool = False
    message_id: str | None = None
    attachments: list[str] = field(default_factory=list)
    #: True when the sender's real JID is hidden by the room (semi/anonymous),
    #: so ``sender_jid`` is a synthetic ``room@conference/nick`` placeholder.
    anonymous: bool = False
    #: The sender's MUC affiliation: ``owner``, ``admin``, ``member`` or
    #: ``none``. Visible even in anonymous rooms, so it is the basis for
    #: owner-only actions.
    affiliation: str = ""

    @property
    def scope_jid(self) -> str:
        return self.room_jid


@dataclass(frozen=True)
class PresenceEvent:
    """A tracked presence update (SPEC §6)."""

    jid: str
    available: bool
    status: str = ""
    show: str = ""


class XMPPTransport(Protocol):
    """Interface implemented by both C2S and component transports."""

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def send_message(
        self,
        to_jid: str,
        body: str,
        *,
        message_type: str = "chat",
        composing: bool = False,
    ) -> None: ...

    async def send_chat_state(self, to_jid: str, state: str) -> None: ...

    async def send_presence(
        self, *, show: str = "", status: str = "", to_jid: str | None = None
    ) -> None: ...

    async def join_muc(self, room_jid: str, nick: str, *, password: str = "") -> None: ...

    async def leave_muc(self, room_jid: str, nick: str, *, status: str = "") -> None: ...

    async def send_muc_message(self, room_jid: str, body: str) -> None: ...
