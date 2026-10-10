"""Markdown → XEP-0394 message markup (SPEC formatting)."""

from __future__ import annotations

import asyncio

import pytest
import slixmpp
from slixmpp.xmlstream import ET, tostring

from agent.xmpp.base import BaseTransport
from agent.xmpp.markup import MarkupBlock, MarkupSpan, markdown_to_markup
from tests.conftest import make_config

# -- converter ---------------------------------------------------------------


def _texts(result, start, end):  # type: ignore[no-untyped-def]
    return result.body[start:end]


def test_plain_text_has_no_markup() -> None:
    result = markdown_to_markup("There is nothing to format here.")
    assert result.body == "There is nothing to format here."
    assert not result.has_markup


def test_emphasis_and_strong() -> None:
    result = markdown_to_markup("Use **bold** and *italic*.")
    assert result.body == "Use bold and italic."
    spans = {s.types[0]: s for s in result.elements if isinstance(s, MarkupSpan)}
    assert spans["strong"].start == 4 and spans["strong"].end == 8
    assert spans["emphasis"].start == 13 and spans["emphasis"].end == 19


def test_underscore_variants() -> None:
    result = markdown_to_markup("_italic_ and __bold__")
    assert result.body == "italic and bold"
    types = sorted(s.types[0] for s in result.elements if isinstance(s, MarkupSpan))
    assert types == ["emphasis", "strong"]


def test_inline_code_and_deleted() -> None:
    result = markdown_to_markup("run `ls -la` and ~~old~~")
    assert result.body == "run ls -la and old"
    types = {s.types[0] for s in result.elements if isinstance(s, MarkupSpan)}
    assert types == {"code", "deleted"}


def test_emphasis_does_not_match_inside_words() -> None:
    # "a*b*c" should not become italic.
    result = markdown_to_markup("file a*b*c name")
    assert result.body == "file a*b*c name"
    assert not result.has_markup


def test_fenced_code_block() -> None:
    result = markdown_to_markup("Run:\n```bash\nls -la\n```\nDone.")
    blocks = [e for e in result.elements if isinstance(e, MarkupBlock)]
    assert len(blocks) == 1
    assert blocks[0].kind == "bcode"
    assert blocks[0].language == "bash"
    assert result.body[blocks[0].start : blocks[0].end] == "ls -la"


def test_unordered_list() -> None:
    result = markdown_to_markup("Items:\n- one\n- two")
    blocks = [e for e in result.elements if isinstance(e, MarkupBlock)]
    assert blocks[0].kind == "list"
    assert blocks[0].ordered is False
    assert len(blocks[0].items) == 2
    assert result.body == "Items:\none\ntwo"


def test_ordered_list() -> None:
    result = markdown_to_markup("1. first\n2. second")
    blocks = [e for e in result.elements if isinstance(e, MarkupBlock)]
    assert blocks[0].kind == "list" and blocks[0].ordered is True


def test_blockquote() -> None:
    result = markdown_to_markup("He said:\n> hi")
    blocks = [e for e in result.elements if isinstance(e, MarkupBlock)]
    assert blocks[0].kind == "bquote"
    assert result.body[blocks[0].start : blocks[0].end] == "hi"


def test_offsets_use_code_points() -> None:
    # Cyrillic counts as one code point per character.
    result = markdown_to_markup("Привет **мир**!")
    assert result.body == "Привет мир!"
    span = next(s for s in result.elements if isinstance(s, MarkupSpan))
    assert result.body[span.start : span.end] == "мир"


# -- transport integration ---------------------------------------------------


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    config = make_config()
    t = BaseTransport(config)
    t.xmpp = slixmpp.ClientXMPP("agent@example.com", "x")
    t.xmpp.register_plugin("xep_0071")
    t.xmpp.register_plugin("xep_0394")
    sent: list[str] = []

    def capture(message, *args, **kwargs):
        sent.append(tostring(message.xml))

    monkeypatch.setattr(slixmpp.stanza.Message, "send", capture)
    return t, sent


def test_outgoing_message_carries_markup(transport) -> None:
    t, sent = transport
    t._send_message_stanza("b@example.com", "Use **bold** and *italic*.", "chat")
    xml = sent[0]
    assert "<body>Use bold and italic.</body>" in xml
    assert 'xmlns="urn:xmpp:markup:0"' in xml
    assert "<strong />" in xml
    assert "<emphasis />" in xml


def test_outgoing_plain_message_has_no_markup(transport) -> None:
    t, sent = transport
    t._send_message_stanza("b@example.com", "Just plain text.", "chat")
    assert "<markup" not in sent[0]
    assert "<body>Just plain text.</body>" in sent[0]


def test_outgoing_muc_message_carries_markup(transport) -> None:
    t, sent = transport
    t._send_message_stanza("room@conference.example.com", "*hi*", "groupchat")
    assert 'type="groupchat"' in sent[0]
    assert "<emphasis />" in sent[0]


def test_markup_offsets_match_body(transport) -> None:
    t, sent = transport
    t._send_message_stanza("b@example.com", "say **now**", "chat")
    xml = ET.fromstring(sent[0])
    body = xml.find("{jabber:client}body")
    assert body is not None
    span = xml.find(".//{urn:xmpp:markup:0}span")
    assert span is not None
    start = int(span.get("start"))
    end = int(span.get("end"))
    assert body.text is not None
    assert body.text[start:end] == "now"
