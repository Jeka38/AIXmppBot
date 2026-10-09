"""HTML/text extraction without heavyweight dependencies.

``html_to_text`` strips scripts/styles/tags and collapses whitespace using the
stdlib parser. ``extract_title`` pulls the ``<title>``. Good enough for feeding
web pages to an LLM; if ``selectolax``/``readability`` become available they can
be swapped in behind :func:`html_to_text`.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser

_BLOCK_TAGS = {
    "p",
    "div",
    "br",
    "li",
    "tr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "section",
    "article",
    "header",
    "footer",
    "blockquote",
    "pre",
}
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "head"}
_WHITESPACE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES = re.compile(r"\n{3,}")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self._title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            if tag == "head":
                return
        if tag == "title":
            self._in_title = True
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        if self._skip_depth:
            return
        self._parts.append(data)

    @property
    def text(self) -> str:
        raw = "".join(self._parts)
        raw = _WHITESPACE.sub(" ", raw)
        lines = [line.strip() for line in raw.splitlines()]
        collapsed = "\n".join(line for line in lines if line)
        return _BLANK_LINES.sub("\n\n", collapsed).strip()

    @property
    def title(self) -> str:
        return _WHITESPACE.sub(" ", "".join(self._title_parts)).strip()


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed HTML should not crash a fetch
        pass
    return parser.text


def extract_title(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001
        pass
    return parser.title


def decode_bytes(body: bytes, content_type: str = "") -> str:
    """Decode bytes to text, honouring a charset in the content type."""
    charset = ""
    if "charset=" in content_type:
        charset = content_type.split("charset=", 1)[1].split(";", 1)[0].strip().strip('"')
    for encoding in filter(None, [charset, "utf-8", "latin-1"]):
        try:
            return body.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return body.decode("utf-8", errors="replace")


def pretty_json(raw: str) -> str:
    try:
        return json.dumps(json.loads(raw), indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, ValueError):
        return raw
