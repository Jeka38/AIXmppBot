"""Render assistant Markdown/plain text into XHTML-IM (XEP-0071).

XEP-0394 is the modern, safer format, but many popular clients (Conversations,
Monocles, Gajim in some configs) only render XHTML-IM. We therefore send both:
``<body>`` (plain, always readable), ``<markup>`` (XEP-0394) and ``<html>``
(XHTML-IM). Each client picks what it supports.

Only a small, safe subset of HTML is produced — ``p``, ``br``, ``strong``,
``em``, ``code``, ``pre``, ``blockquote``, ``ul``/``ol``/``li`` — and all text is
HTML-escaped, so no markup or script from the model or from external content can
leak through.
"""

from __future__ import annotations

import re
from html import escape

from agent.xmpp.markup import (
    MarkupResult,
    markdown_to_markup,
)

_XHTML_NS = "http://www.w3.org/1999/xhtml"


def render_xhtml_im(text: str) -> str:
    """Return an XHTML-IM ``<body>`` inner fragment for *text*."""
    return _render(markdown_to_markup(text))


def _render(markup: MarkupResult) -> str:
    """Turn a :class:`MarkupResult` into escaped XHTML-IM content."""
    body = markup.body
    if not markup.has_markup:
        return _paragraphs(body)
    # Markup offsets are code points into the plain body; walk them in order,
    # opening/closing tags as we go, and escape the text between.
    events = _events(markup)
    out: list[str] = []
    position = 0
    for offset, _priority, tag in events:
        offset = max(0, min(offset, len(body)))
        if offset > position:
            out.append(_escape_with_breaks(body[position:offset]))
            position = offset
        out.append(tag)
    if position < len(body):
        out.append(_escape_with_breaks(body[position:]))
    html = "".join(out)
    # Strip the trailing <br/> that the newline separating block elements adds.
    html = re.sub(r"<br/>(\s*</(?:li|blockquote|pre)>)", r"\1", html)
    if not any(getattr(e, "kind", None) for e in markup.elements):
        # Inline-only content: wrap in a paragraph so the client renders it.
        return f"<p>{html}</p>"
    return html


#: Event priorities. Lower runs first at the same offset. A closing ``</li>``
#: must precede the next opening ``<li>``; block opens precede inline opens.
_P_CLOSE_LI = 0
_P_OPEN_BLOCK = 1
_P_OPEN_LI = 2
_P_OPEN_SPAN = 3
_P_CLOSE_SPAN = 4
_P_CLOSE_BLOCK = 5


def _events(markup: MarkupResult) -> list[tuple[int, int, str]]:
    """Build a sorted open/close event list for all markup elements."""
    events: list[tuple[int, int, str]] = []  # (offset, priority, tag)
    for element in markup.elements:
        if hasattr(element, "types"):  # MarkupSpan
            open_tags = "".join(_SPAN_OPEN[t] for t in element.types)
            close_tags = "".join(reversed([_SPAN_CLOSE[t] for t in element.types]))
            events.append((element.start, _P_OPEN_SPAN, open_tags))
            events.append((element.end, _P_CLOSE_SPAN, close_tags))
        elif element.kind == "bcode":
            lang = f' class="language-{escape(element.language)}"' if element.language else ""
            events.append((element.start, _P_OPEN_BLOCK, f"<pre><code{lang}>"))
            events.append((element.end, _P_CLOSE_BLOCK, "</code></pre>"))
        elif element.kind == "bquote":
            events.append((element.start, _P_OPEN_BLOCK, "<blockquote>"))
            events.append((element.end, _P_CLOSE_BLOCK, "</blockquote>"))
        elif element.kind == "list":
            tag = "ol" if element.ordered else "ul"
            events.append((element.start, _P_OPEN_BLOCK, f"<{tag}>"))
            starts = element.items
            for i, start in enumerate(starts):
                end = starts[i + 1] if i + 1 < len(starts) else element.end
                events.append((start, _P_OPEN_LI, "<li>"))
                events.append((end, _P_CLOSE_LI, "</li>"))
            events.append((element.end, _P_CLOSE_BLOCK, f"</{tag}>"))
    events.sort(key=lambda e: (e[0], e[1]))
    return events


def _paragraphs(text: str) -> str:
    blocks = [block for block in text.split("\n\n")]
    rendered = "".join(f"<p>{_escape_with_breaks(block)}</p>" for block in blocks if block.strip())
    return rendered or "<p></p>"


def _escape_with_breaks(text: str) -> str:
    return escape(text, quote=False).replace("\n", "<br/>")


_SPAN_OPEN = {
    "strong": "<strong>",
    "emphasis": "<em>",
    "code": "<code>",
    "deleted": "<del>",
}
_SPAN_CLOSE = {
    "strong": "</strong>",
    "emphasis": "</em>",
    "code": "</code>",
    "deleted": "</del>",
}
