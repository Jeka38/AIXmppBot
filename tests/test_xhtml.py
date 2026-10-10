"""XHTML-IM (XEP-0071) rendering, for clients without XEP-0394 support."""

from __future__ import annotations

import asyncio

import pytest
import slixmpp
from slixmpp.xmlstream import ET, tostring

from agent.xmpp.base import BaseTransport
from agent.xmpp.xhtml import render_xhtml_im
from tests.conftest import make_config


def test_plain_text_is_a_paragraph() -> None:
    assert render_xhtml_im("hello") == "<p>hello</p>"


def test_inline_markup() -> None:
    html = render_xhtml_im("Use **bold** and *italic* and `code`.")
    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html
    assert "<code>code</code>" in html


def test_plain_paragraphs() -> None:
    html = render_xhtml_im("first\n\nsecond")
    assert html == "<p>first</p><p>second</p>"


def test_unordered_list() -> None:
    html = render_xhtml_im("- one\n- two")
    assert html == "<ul><li>one</li><li>two</li></ul>"


def test_ordered_list() -> None:
    html = render_xhtml_im("1. first\n2. second")
    assert html == "<ol><li>first</li><li>second</li></ol>"


def test_code_block() -> None:
    html = render_xhtml_im("Run:\n```bash\nls -la\n```")
    assert '<pre><code class="language-bash">ls -la</code></pre>' in html


def test_blockquote() -> None:
    html = render_xhtml_im("> quoted")
    assert "<blockquote>quoted</blockquote>" in html


def test_html_is_escaped() -> None:
    # Model output and external content must never inject markup.
    html = render_xhtml_im("safe <script>alert(1)</script> & <b>x</b>")
    assert "<script>" not in html
    assert "&lt;script&gt;" not in html  # tags stripped, not re-added
    assert "&amp;" in html
    assert "<b>x</b>" not in html


def test_inline_html_list_normalised() -> None:
    # Models sometimes emit <ul><li>…; the plain body and XHTML must stay clean.
    html = render_xhtml_im("<ul><li>one</li><li>two</li></ul>")
    assert "<li>one</li>" in html and "<li>two</li>" in html


def test_markdown_table_flattened() -> None:
    text = "| Раздел | Описание |\n|---|---|\n| Звонки | плитка |"
    html = render_xhtml_im(text)
    assert "|" not in html
    assert "Раздел: Звонки" in html


# -- stanza integration ------------------------------------------------------


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


def test_stanza_has_plain_markup_and_html(transport) -> None:
    t, sent = transport
    t._send_message_stanza("b@example.com", "Use **bold** and *italic*.", "chat")
    xml = sent[0]
    assert "<body>" in xml  # plain fallback
    assert 'xmlns="urn:xmpp:markup:0"' in xml  # XEP-0394
    assert 'xmlns="http://jabber.org/protocol/xhtml-im"' in xml  # XEP-0071
    root = ET.fromstring(xml)
    body = root.find("{jabber:client}body")
    assert body is not None and body.text == "Use bold and italic."
    strong = root.find(
        ".//{http://jabber.org/protocol/xhtml-im}html"
        "/{http://www.w3.org/1999/xhtml}body"
        "/{http://www.w3.org/1999/xhtml}p"
        "/{http://www.w3.org/1999/xhtml}strong"
    )
    assert strong is not None
    assert strong.text == "bold"
