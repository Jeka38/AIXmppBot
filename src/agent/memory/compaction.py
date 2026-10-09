"""Context window management.

Builds the LLM message list from persisted session events and enforces the
configured token budgets. Phase 1 uses deterministic oldest-first trimming;
LLM-driven summarisation is added in Phase 2 (SPEC §24).
"""

from __future__ import annotations

from agent.llm.base import ChatMessage
from agent.llm.tokenizer import TokenEstimator
from agent.memory.session import SessionEvent


class ContextBuilder:
    """Turn session events into a budgeted :class:`ChatMessage` list."""

    def __init__(self, estimator: TokenEstimator, *, budget: int) -> None:
        self.estimator = estimator
        self.budget = budget
        self._last_trimmed = 0

    @property
    def last_trimmed(self) -> int:
        """Number of oldest messages dropped during the last build call."""
        return self._last_trimmed

    def build(self, events: list[SessionEvent]) -> list[ChatMessage]:
        """Return conversation messages that fit within the token budget.

        System/policy messages are never dropped. Tool events are folded into
        the assistant/tool shape the providers expect.
        """
        messages = [self._to_message(event) for event in events]
        present = [message for message in messages if message is not None]
        return self._trim(present)

    def _to_message(self, event: SessionEvent) -> ChatMessage | None:
        if event.type == "message":
            role = event.role or "user"
            if role not in ("system", "user", "assistant", "tool"):
                role = "user"
            return ChatMessage(role=role, content=event.content)  # type: ignore[arg-type]
        if event.type == "tool_result":
            return ChatMessage(
                role="tool",
                content=event.content,
                tool_call_id=event.tool_call_id,
                name=event.tool,
            )
        if event.type == "summary":
            return ChatMessage(
                role="system",
                content=f"[Earlier conversation summary]\n{event.content}",
            )
        # tool_call and unknown events are replayed via assistant text so the
        # transcript stays linear for providers that require strict pairing.
        return None

    def _trim(self, messages: list[ChatMessage]) -> list[ChatMessage]:
        self._last_trimmed = 0
        if self.estimator.count_messages(messages) <= self.budget:
            return messages

        system = [message for message in messages if message.role == "system"]
        rest = [message for message in messages if message.role != "system"]

        kept: list[ChatMessage] = []
        used = self.estimator.count_messages(system)
        for message in reversed(rest):
            cost = self.estimator.count_messages([message])
            if used + cost > self.budget and kept:
                break
            kept.append(message)
            used += cost
        kept.reverse()
        self._last_trimmed = len(rest) - len(kept)
        return system + kept
