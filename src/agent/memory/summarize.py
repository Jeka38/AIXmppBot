"""LLM-driven context compaction (SPEC §24).

When a session's events exceed its token budget, the oldest events are folded
into a single ``summary`` event. The summary preserves the facts the SPEC calls
out: facts, decisions, user preferences, project state, important tool results,
and — for MUC — the author of every important message.

The compactor never drops the most recent events, and it is fail-soft: if the
LLM call fails, the session is left untouched rather than losing history.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from agent.errors import LLMError
from agent.llm.base import ChatMessage
from agent.llm.router import LLMRouter
from agent.llm.tokenizer import TokenEstimator
from agent.memory.session import SessionEvent, utc_now_iso

log = logging.getLogger(__name__)

_SUMMARY_SYSTEM = (
    "You compress conversation history for an AI assistant. "
    "Produce a dense, factual summary that preserves:\n"
    "- facts and decisions\n"
    "- user preferences and constraints\n"
    "- project state and ongoing tasks\n"
    "- important tool results (keep concrete values)\n"
    "- for group chats: WHO said each important thing\n"
    "Write in the third person. Do not invent information. Be concise."
)


@dataclass
class CompactionResult:
    compacted: bool
    summarised: int = 0
    summary_tokens: int = 0


class Compactor:
    def __init__(
        self,
        router: LLMRouter,
        estimator: TokenEstimator,
        *,
        keep_recent: int = 20,
    ) -> None:
        self.router = router
        self.estimator = estimator
        self.keep_recent = keep_recent

    async def compact(
        self, events: list[SessionEvent], *, budget: int
    ) -> tuple[list[SessionEvent], CompactionResult]:
        """Return ``(new_events, result)``.

        If the budget is respected or the summarisation fails, ``new_events`` is
        the original list and ``compacted`` is ``False``.
        """
        messages = self._to_messages(events)
        if self.estimator.count_messages(messages) <= budget:
            return events, CompactionResult(compacted=False)

        split = self._split_index(events)
        if split <= 0:
            return events, CompactionResult(compacted=False)

        older = events[:split]
        recent = events[split:]
        try:
            summary_text = await self._summarise(older)
        except LLMError as exc:
            log.warning("Compaction summarisation failed: %s", exc)
            return events, CompactionResult(compacted=False)

        summary_event = SessionEvent(
            type="summary",
            compacted_count=len(older),
            content=summary_text,
            timestamp=utc_now_iso(),
        )
        new_events = [summary_event, *recent]
        return new_events, CompactionResult(
            compacted=True,
            summarised=len(older),
            summary_tokens=self.estimator.count_text(summary_text),
        )

    def _split_index(self, events: list[SessionEvent]) -> int:
        if len(events) <= self.keep_recent:
            return 0
        # Never place the split between a tool call and its result: move the
        # boundary forward until the recent window starts cleanly.
        split = len(events) - self.keep_recent
        while split < len(events) and events[split].type == "tool_result":
            split += 1
        return split if split < len(events) else 0

    def _to_messages(self, events: list[SessionEvent]) -> list[ChatMessage]:
        result: list[ChatMessage] = []
        for event in events:
            if event.type == "summary":
                result.append(ChatMessage(role="system", content=event.content))
            elif event.type == "tool_result":
                result.append(ChatMessage(role="tool", content=event.content))
            elif event.type == "message":
                role = event.role or "user"
                if role not in ("system", "user", "assistant", "tool"):
                    role = "user"
                result.append(ChatMessage(role=role, content=event.content))  # type: ignore[arg-type]
        return result

    async def _summarise(self, events: list[SessionEvent]) -> str:
        transcript = "\n".join(_render_event(event) for event in events)
        response = await self.router.chat(
            [
                ChatMessage(role="system", content=_SUMMARY_SYSTEM),
                ChatMessage(
                    role="user",
                    content=f"Summarise the following conversation history:\n\n{transcript}",
                ),
            ],
            tier="light",
        )
        return response.content.strip()


def _render_event(event: SessionEvent) -> str:
    author = event.sender or event.role or event.tool or "unknown"
    if event.type == "tool_call":
        return f"[tool_call:{event.tool}] {event.arguments}"
    if event.type == "tool_result":
        return f"[tool_result:{event.tool}] {(event.content or '')[:800]}"
    if event.type == "summary":
        return f"[prior summary] {event.content}"
    return f"[{author}] {event.content}"
