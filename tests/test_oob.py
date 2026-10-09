"""XEP-0066 OOB extraction without loading the plugin interface."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from slixmpp.stanza import Message

from agent.xmpp.base import _OOB_NS, _extract_oob


def test_extract_oob_urls_from_xml() -> None:
    xml = (
        '<message type="chat" from="alice@example.com">'
        "<body>see this</body>"
        '<x xmlns="jabber:x:oob">'
        "<url>https://example.com/file.pdf</url>"
        "</x>"
        "</message>"
    )
    msg = Message(xml=ET.fromstring(xml))
    assert _extract_oob(msg) == ["https://example.com/file.pdf"]


def test_extract_oob_multiple_urls() -> None:
    xml = (
        '<message type="chat">'
        '<x xmlns="jabber:x:oob">'
        "<url>https://example.com/a.png</url>"
        "<url>https://example.com/b.pdf</url>"
        "</x>"
        "</message>"
    )
    msg = Message(xml=ET.fromstring(xml))
    assert _extract_oob(msg) == [
        "https://example.com/a.png",
        "https://example.com/b.pdf",
    ]


def test_extract_oob_empty_when_absent() -> None:
    msg = Message()
    msg["type"] = "chat"
    msg["body"] = "hello"
    assert _extract_oob(msg) == []


def test_extract_oob_ignores_unrelated_x_element() -> None:
    xml = (
        '<message type="chat">'
        '<x xmlns="http://jabber.org/protocol/muc#user">'
        "<url>https://example.com/not-oob</url>"
        "</x>"
        "</message>"
    )
    msg = Message(xml=ET.fromstring(xml))
    assert _extract_oob(msg) == []


def test_oob_namespace_constant() -> None:
    assert _OOB_NS == "jabber:x:oob"
