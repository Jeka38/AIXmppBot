"""Token estimation used for context budgeting.

Prefers ``tiktoken`` when the optional dependency is installed; otherwise uses
a conservative character heuristic (over-estimating is safer than
under-estimating when trimming history).
"""

from __future__ import annotations

import math
from typing import Any

try:  # pragma: no cover - optional dependency
    import tiktoken

    _HAVE_TIKTOKEN = True
except Exception:  # pragma: no cover - import guarded for environments without it
    _HAVE_TIKTOKEN = False


class TokenEstimator:
    """Estimate token counts for strings and chat messages."""

    #: Rough per-message overhead imposed by chat formatting.
    MESSAGE_OVERHEAD = 4

    def __init__(self, model: str = "") -> None:
        self.model = model
        self._encoder: Any = None
        if _HAVE_TIKTOKEN:
            try:
                self._encoder = tiktoken.encoding_for_model(model or "gpt-4o")
            except Exception:
                try:
                    self._encoder = tiktoken.get_encoding("cl100k_base")
                except Exception:
                    self._encoder = None

    def count_text(self, text: str) -> int:
        if not text:
            return 0
        if self._encoder is not None:
            return len(self._encoder.encode(text))
        return _heuristic_count(text)

    def count_messages(self, messages: list[Any]) -> int:
        total = 0
        for message in messages:
            content = getattr(message, "content", "") or ""
            total += self.count_text(content) + self.MESSAGE_OVERHEAD
            images = getattr(message, "images", None)
            if images:
                # A base64 image is expensive; approximate per-image budget.
                total += 850
            tool_calls = getattr(message, "tool_calls", None)
            if tool_calls:
                for call in tool_calls:
                    total += self.count_text(getattr(call, "name", ""))
                    total += self.count_text(str(getattr(call, "arguments", "")))
        return total + 2


def _heuristic_count(text: str) -> int:
    """Conservative estimate: ASCII ~4 chars/token, other scripts ~1 char/token."""
    ascii_count = 0
    other_count = 0
    for char in text:
        if char.isascii():
            ascii_count += 1
        else:
            other_count += 1
    return max(1, math.ceil(ascii_count / 4) + other_count)
