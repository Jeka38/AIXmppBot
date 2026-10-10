"""Convert assistant Markdown into XEP-0394 message markup.

The bot's answers are Markdown. XEP-0394 separates the *text* (``<body>``) from
*markup* (``<markup>``) and locates styling by unicode code-point offsets, which
avoids the spoofing/injection problems of XHTML-IM.

This module parses the small Markdown subset the model actually emits and returns
the plain body plus a list of :class:`MarkupSpan`/:class:`MarkupBlock` records.
The transport layer turns those into Slixmpp's ``urn:xmpp:markup:0`` elements.

Supported: ``**bold**``/``__bold__`` (strong), ``*italic*``/``_italic_``
(emphasis), ``` `code` ``` (inline code), ``~~deleted~~``, fenced ``` code
blocks, ``> blockquote`` and ``-``/``*``/``1.`` lists.

Anything unrecognised is passed through as literal text, so the plain body is
always a faithful, readable representation even for clients without markup.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

SpanType = Literal["emphasis", "strong", "code", "deleted"]


@dataclass
class MarkupSpan:
    """An inline span over ``body[start:end]``."""

    start: int
    end: int
    types: list[SpanType] = field(default_factory=list)


@dataclass
class MarkupBlock:
    """A block-level element (code block, list, quote)."""

    kind: Literal["bcode", "list", "bquote"]
    start: int
    end: int
    language: str = ""
    ordered: bool = False
    #: Start offsets of list items (absolute code-point positions in the body).
    items: list[int] = field(default_factory=list)


MarkupElement = MarkupSpan | MarkupBlock


@dataclass
class MarkupResult:
    body: str
    elements: list[MarkupElement] = field(default_factory=list)

    @property
    def has_markup(self) -> bool:
        return bool(self.elements)


# Inline patterns, applied in order. Each captures the inner text.
_INLINE: list[tuple[SpanType, re.Pattern[str]]] = [
    ("code", re.compile(r"`([^`\n]+)`")),
    ("strong", re.compile(r"\*\*([^*\n]+)\*\*|__([^_\n]+)__")),
    ("deleted", re.compile(r"~~([^~\n]+)~~")),
    ("emphasis", re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])|(?<![\w_])_([^_\n]+)_(?![\w_])")),
]

_FENCE = re.compile(r"^```([\w.+#-]*)\s*$")
_LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_QUOTE = re.compile(r"^>\s?(.*)$")
_TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
# Loose HTML the model sometimes emits; we keep the text, drop the tags.
_HTML_TAG = re.compile(r"</?[a-zA-Z][^>]*>")
_HTML_LI = re.compile(r"<li[^>]*>", re.I)


def markdown_to_markup(text: str) -> MarkupResult:
    """Parse *text* into a plain body plus XEP-0394 markup elements."""
    builder = _Builder()
    builder.add_text(_normalize_html(text))
    builder.finish()
    return MarkupResult(body=builder.body, elements=builder.elements)


def _normalize_html(text: str) -> str:
    """Convert stray HTML the model emits into Markdown, then drop any tags.

    Models sometimes wrap content in ``<ul><li>…</li></ul>`` or ``<br>``. Clients
    that only render the plain body must never see raw tags. Table rows are left
    for the table parser, which strips tags per cell.
    """
    out: list[str] = []
    for line in text.split("\n"):
        if _TABLE_ROW.match(line):
            out.append(line)
            continue
        line = re.sub(r"<li[^>]*>", "\n- ", line, flags=re.I)
        line = re.sub(r"</li\s*>", "", line, flags=re.I)
        line = re.sub(r"<br\s*/?>", "\n", line, flags=re.I)
        line = re.sub(r"</p\s*>", "\n", line, flags=re.I)
        line = _HTML_TAG.sub("", line)
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out))


class _Builder:
    """Accumulates body text while tracking absolute code-point offsets."""

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._length = 0
        self.elements: list[MarkupElement] = []

    @property
    def body(self) -> str:
        return "".join(self._parts)

    def add_text(self, text: str) -> None:
        lines = text.split("\n")
        index = 0
        while index < len(lines):
            line = lines[index]
            fence = _FENCE.match(line)
            if fence:
                index = self._consume_code_block(lines, index, fence.group(1))
                continue
            if _QUOTE.match(line):
                index = self._consume_blockquote(lines, index)
                continue
            if self._is_table_start(lines, index):
                index = self._consume_table(lines, index)
                continue
            heading = _HEADING.match(line)
            if heading:
                self._emit_inline(heading.group(2))
                if index != len(lines) - 1:
                    self._append("\n")
                index += 1
                continue
            if _LIST_ITEM.match(line):
                index = self._consume_list(lines, index)
                continue
            self._emit_inline(line)
            if index != len(lines) - 1:
                self._append("\n")
            index += 1

    # -- block elements --------------------------------------------------

    @staticmethod
    def _is_table_start(lines: list[str], index: int) -> bool:
        """A table is a header row followed by a ``|---|---|`` separator."""
        if index + 1 >= len(lines):
            return False
        if not _TABLE_ROW.match(lines[index]):
            return False
        return bool(_TABLE_SEP.match(lines[index + 1]))

    def _consume_table(self, lines: list[str], start: int) -> int:
        """Render a Markdown table as ``header: value`` lines.

        XEP-0394 has no table element, and a raw ``| a | b |`` grid is
        unreadable in clients that only show plain text, so we flatten each row
        into a readable ``header: cell`` block.
        """
        headers = self._split_row(lines[start])
        index = start + 2  # skip header and separator
        while index < len(lines) and _TABLE_ROW.match(lines[index]):
            cells = self._split_row(lines[index])
            pairs = [
                f"{header}: {cell}".strip(": ").strip() if header else cell
                for header, cell in zip(headers, cells, strict=False)
            ]
            self._emit_inline("; ".join(p for p in pairs if p))
            index += 1
            if index < len(lines) and _TABLE_ROW.match(lines[index]):
                self._append("\n")
        if index < len(lines):
            self._append("\n")
        return index

    @staticmethod
    def _split_row(line: str) -> list[str]:
        inner = _TABLE_ROW.match(line)
        if inner is None:
            return []
        cells = []
        for cell in inner.group(1).split("|"):
            # Flatten any inline HTML list inside a cell into a comma list.
            cell = re.sub(r"</li\s*>", ", ", cell, flags=re.I)
            cell = _HTML_TAG.sub("", cell).strip(" ,")
            cells.append(cell.strip())
        return cells

    def _consume_code_block(self, lines: list[str], start: int, language: str) -> int:
        index = start + 1
        content: list[str] = []
        while index < len(lines) and not _FENCE.match(lines[index]):
            content.append(lines[index])
            index += 1
        close = index if index < len(lines) else len(lines) - 1
        body_start = self._length
        self._append("\n".join(content))
        self.elements.append(
            MarkupBlock(kind="bcode", start=body_start, end=self._length, language=language)
        )
        if close != start:
            self._append("\n")
        return close + 1

    def _consume_blockquote(self, lines: list[str], start: int) -> int:
        body_start = self._length
        index = start
        while index < len(lines) and (match := _QUOTE.match(lines[index])):
            self._emit_inline(match.group(1))
            index += 1
            if index < len(lines) and _QUOTE.match(lines[index]):
                self._append("\n")
        self.elements.append(MarkupBlock(kind="bquote", start=body_start, end=self._length))
        if index < len(lines):
            self._append("\n")
        return index

    def _consume_list(self, lines: list[str], start: int) -> int:
        body_start = self._length
        items: list[int] = []
        ordered = False
        index = start
        while index < len(lines):
            match = _LIST_ITEM.match(lines[index])
            if not match:
                break
            marker = match.group(2)
            if marker[0].isdigit():
                ordered = True
            items.append(self._length)
            self._emit_inline(match.group(3))
            index += 1
            if index < len(lines) and _LIST_ITEM.match(lines[index]):
                self._append("\n")
        self.elements.append(
            MarkupBlock(
                kind="list",
                start=body_start,
                end=self._length,
                ordered=ordered,
                items=items,
            )
        )
        if index < len(lines):
            self._append("\n")
        return index

    # -- inline ----------------------------------------------------------

    def _emit_inline(self, line: str) -> None:
        """Emit *line*, recording inline spans and stripping their markers."""
        # We scan left to right, matching the vocabulary at each position.
        position = 0
        while position < len(line):
            found = _match_inline(line, position)
            if found is None:
                self._append(line[position])
                position += 1
                continue
            (start, end, inner), span_type = found
            body_start = self._length
            self._emit_inline(inner)
            self.elements.append(MarkupSpan(start=body_start, end=self._length, types=[span_type]))
            position = end

    def _append(self, text: str) -> None:
        if not text:
            return
        self._parts.append(text)
        self._length += len(text)

    def finish(self) -> None:
        # Nothing to do; kept for symmetry / future normalisation.
        return


def _match_inline(line: str, position: int) -> tuple[tuple[int, int, str], SpanType] | None:
    for span_type, pattern in _INLINE:
        match = pattern.match(line, position)
        if match:
            inner = next((g for g in match.groups() if g is not None), "")
            return (match.start(), match.end(), inner), span_type
    return None
