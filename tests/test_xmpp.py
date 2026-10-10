"""Transport-layer parsing of XMPP stanzas (Slixmpp integration)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from slixmpp.stanza import Message

from agent.config.models import RoomConfig
from agent.xmpp.base import _is_mention, _muc_nick, _presence_jid, strip_mention
from agent.xmpp.client import C2STransport
from agent.xmpp.component import ComponentTransport
from agent.xmpp.events import MessageEvent, MUCMessageEvent
from tests.conftest import make_config


def _chat_message(sender: str, body: str) -> Message:
    msg = Message()
    msg["from"] = sender
    msg["type"] = "chat"
    msg["body"] = body
    msg["id"] = "msg-1"
    return msg


async def test_dm_message_decoded() -> None:
    transport = C2STransport(make_config())
    seen: list[MessageEvent] = []
    transport.on_message = lambda event: _capture(seen, event)

    await transport._on_message(_chat_message("alice@example.com/res", "hi there"))

    assert len(seen) == 1
    assert seen[0].sender_jid == "alice@example.com"
    assert seen[0].text == "hi there"
    assert seen[0].message_id == "msg-1"


async def test_empty_body_ignored() -> None:
    transport = C2STransport(make_config())
    seen: list[MessageEvent] = []
    transport.on_message = lambda event: _capture(seen, event)
    await transport._on_message(_chat_message("alice@example.com", ""))
    assert seen == []


async def test_non_chat_type_ignored() -> None:
    transport = C2STransport(make_config())
    seen: list[MessageEvent] = []
    transport.on_message = lambda event: _capture(seen, event)
    msg = _chat_message("alice@example.com", "hi")
    msg["type"] = "groupchat"
    await transport._on_message(msg)
    assert seen == []


async def _capture(store: list, event) -> None:  # type: ignore[type-arg]
    store.append(event)


def test_mention_detection() -> None:
    room = RoomConfig(
        jid="room@conference.example.com",
        nick="AI-Agent",
        aliases=["agent", "bot"],
    )
    assert _is_mention("hey @ai-agent can you help", room)
    assert _is_mention("AI-Agent, what do you think?", room)
    assert _is_mention("agent: summarize this", room)  # alias
    assert not _is_mention("just chatting among ourselves", room)


def test_mention_is_word_bounded() -> None:
    room = RoomConfig(jid="room@conference.example.com", nick="AI")
    # "AI" as a substring must not trigger a mention.
    assert not _is_mention("said the email contained details", room)
    assert not _is_mention("waiting patiently", room)
    assert _is_mention("AI: help me", room)
    assert _is_mention("@ai help me", room)
    assert _is_mention("please ai help", room)


def test_strip_mention_variants() -> None:
    room = RoomConfig(jid="room@conference.example.com", nick="AI", aliases=["bot"])
    assert strip_mention("AI: /persona list", room) == "/persona list"
    assert strip_mention("@AI help me", room) == "help me"
    assert strip_mention("AI, hello", room) == "hello"
    assert strip_mention("bot привет", room) == "привет"
    assert strip_mention("просто сообщение", room) == "просто сообщение"
    # Not a mention: the nick inside a word is preserved.
    assert strip_mention("said email", room) == "said email"
    # Only a leading mention is stripped.
    assert strip_mention("помоги мне, AI", room) == "помоги мне, AI"


def test_presence_jid_extraction() -> None:
    pres = _make_presence()
    assert _presence_jid(pres) is None
    assert _muc_nick(pres) == "alice"

    jid_pres = _make_presence(item_jid="alice@example.com")
    assert _presence_jid(jid_pres) == "alice@example.com"


async def test_component_transport_builds() -> None:
    config = make_config()
    config.xmpp.mode = "component"
    config.xmpp.component_jid = "agent.example.com"
    config.xmpp.secret = "shh"
    config.xmpp.host = "127.0.0.1"
    config.xmpp.port = 5347
    transport = ComponentTransport(config)
    xmpp = transport._build_stream()
    assert xmpp.boundjid.bare == "agent.example.com"
    assert xmpp.server_host == "127.0.0.1"
    assert xmpp.server_port == 5347


async def test_c2s_transport_builds() -> None:
    config = make_config()
    transport = C2STransport(config)
    xmpp = transport._build_stream()
    assert xmpp.boundjid.bare == "agent@example.com"
    assert xmpp.enable_starttls is True


def _make_presence(item_jid: str | None = None):
    from slixmpp.stanza import Presence

    item = f' jid="{item_jid}"' if item_jid else ""
    xml = (
        '<presence from="room@conference.example.com/alice" '
        'to="agent@example.com">'
        '<x xmlns="http://jabber.org/protocol/muc#user">'
        f'<item{item} affiliation="member" role="participant"/>'
        "</x></presence>"
    )
    return Presence(xml=ET.fromstring(xml))


async def test_groupchat_message_decoded() -> None:
    config = make_config()
    config.rooms = [RoomConfig(jid="room@conference.example.com", nick="AI-Agent")]
    transport = C2STransport(config)
    seen: list[tuple[RoomConfig, MUCMessageEvent]] = []

    async def on_muc(room: RoomConfig, event: MUCMessageEvent) -> None:
        seen.append((room, event))

    transport.on_muc_message = on_muc

    msg = Message()
    msg["from"] = "room@conference.example.com/Alice"
    msg["type"] = "groupchat"
    msg["body"] = "@AI-Agent help"
    msg["mucnick"] = "Alice"
    msg["id"] = "gc-1"
    await transport._on_groupchat_message(msg)

    assert len(seen) == 1
    room, event = seen[0]
    assert room.jid == "room@conference.example.com"
    assert event.nick == "Alice"
    assert event.mentioned
    # The leading mention is stripped before the text reaches the runtime.
    assert event.text == "help"
    # No muc#user/item -> real JID unknown -> anonymous placeholder + flag.
    assert event.anonymous is True
    assert event.sender_jid == "room@conference.example.com/Alice"


async def test_groupchat_real_jid_detected() -> None:
    config = make_config()
    config.rooms = [RoomConfig(jid="room@conference.example.com", nick="AI-Agent")]
    transport = C2STransport(config)
    seen: list[MUCMessageEvent] = []

    async def on_muc(room: RoomConfig, event: MUCMessageEvent) -> None:
        seen.append(event)

    transport.on_muc_message = on_muc
    xml = (
        '<message xmlns="jabber:client" '
        'from="room@conference.example.com/Alice" type="groupchat" id="gc-2">'
        "<body>@AI-Agent hi</body>"
        '<x xmlns="http://jabber.org/protocol/muc#user">'
        '<item jid="alice@example.com" affiliation="member" role="participant"/>'
        "</x></message>"
    )
    await transport._on_groupchat_message(Message(xml=ET.fromstring(xml)))

    assert len(seen) == 1
    assert seen[0].anonymous is False
    assert seen[0].sender_jid == "alice@example.com"


async def test_groupchat_ignores_own_messages() -> None:
    config = make_config()
    config.rooms = [RoomConfig(jid="room@conference.example.com", nick="AI-Agent")]
    transport = C2STransport(config)
    seen: list[MUCMessageEvent] = []

    async def on_muc(room: RoomConfig, event: MUCMessageEvent) -> None:
        seen.append(event)

    transport.on_muc_message = on_muc
    msg = Message()
    msg["from"] = "room@conference.example.com/AI-Agent"
    msg["type"] = "groupchat"
    msg["body"] = "I am the bot"
    msg["mucnick"] = "AI-Agent"
    await transport._on_groupchat_message(msg)

    # The bot must never treat its own room message as input (loop prevention).
    assert seen == []


# -- roster subscription -----------------------------------------------------


def _subscription_presence(sender: str) -> object:
    from slixmpp.stanza import Presence

    pres = Presence()
    pres["from"] = sender
    pres["to"] = "agent@example.com"
    pres["type"] = "subscribe"
    return pres


def test_subscription_approved_for_allowed() -> None:
    transport = C2STransport(make_config())
    transport.subscription_decider = lambda jid: jid == "alice@example.com"
    # Minimal fake roster item capturing authorize/unauthorize.
    approved: list[bool] = []

    class Item:
        def authorize(self) -> None:
            approved.append(True)

        def unauthorize(self) -> None:
            approved.append(False)

    class Roster:
        def __getitem__(self, key):  # noqa: ANN001
            return Item()

    class Xmpp:
        roster = {"agent@example.com": Roster()}

    transport.xmpp = Xmpp()  # type: ignore[assignment]
    transport._on_subscription_request(_subscription_presence("alice@example.com"))
    assert approved == [True]


def test_subscription_rejected_for_unknown() -> None:
    transport = C2STransport(make_config())
    transport.subscription_decider = lambda jid: jid == "alice@example.com"
    approved: list[bool] = []

    class Item:
        def authorize(self) -> None:
            approved.append(True)

        def unauthorize(self) -> None:
            approved.append(False)

    class Roster:
        def __getitem__(self, key):  # noqa: ANN001
            return Item()

    class Xmpp:
        roster = {"agent@example.com": Roster()}

    transport.xmpp = Xmpp()  # type: ignore[assignment]
    transport._on_subscription_request(_subscription_presence("stranger@unknown.test"))
    assert approved == [False]


def test_subscription_no_decider_does_nothing() -> None:
    transport = C2STransport(make_config())
    transport.subscription_decider = None
    # Must not raise even with no roster configured.
    transport.xmpp = None
    transport._on_subscription_request(_subscription_presence("alice@example.com"))
