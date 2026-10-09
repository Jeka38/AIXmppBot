"""Conversation sessions backed by JSONL transcripts (SPEC §15, §16).

A session is identified by a *scope*: ``users/<jid>`` for DMs and
``rooms/<room-jid>`` for MUCs. Scopes are isolated by construction — one
session can never load another's events.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from agent.llm.base import ChatMessage
from agent.llm.tokenizer import TokenEstimator
from agent.memory.compaction import ContextBuilder
from agent.memory.session import SessionEvent, utc_now_iso
from agent.memory.store import FilesystemSessionStore

log = logging.getLogger(__name__)


@dataclass
class Session:
    """In-memory view of one scope's transcript, persisted after each event."""

    scope: str
    store: FilesystemSessionStore
    estimator: TokenEstimator
    budget: int
    events: list[SessionEvent] = field(default_factory=list)

    @classmethod
    def load(
        cls,
        scope: str,
        store: FilesystemSessionStore,
        estimator: TokenEstimator,
        *,
        budget: int,
    ) -> Session:
        events, skipped = store.load_lenient(scope)
        if skipped:
            log.warning("Skipped %d corrupt event(s) while loading session %s", skipped, scope)
        return cls(
            scope=scope,
            store=store,
            estimator=estimator,
            budget=budget,
            events=events,
        )

    # -- mutation --------------------------------------------------------

    def append(self, event: SessionEvent) -> None:
        if not event.timestamp:
            event.timestamp = utc_now_iso()
        self.events.append(event)
        self.store.append(self.scope, event)

    def add_user(self, content: str, sender: str, message_id: str | None = None) -> None:
        self.append(
            SessionEvent(
                type="message",
                role="user",
                sender=sender,
                content=content,
                message_id=message_id,
            )
        )

    def add_assistant(self, content: str) -> None:
        self.append(SessionEvent(type="message", role="assistant", content=content))

    def add_system(self, content: str) -> None:
        self.append(SessionEvent(type="message", role="system", content=content))

    def add_tool_call(
        self, tool: str, arguments: dict[str, object], call_id: str | None = None
    ) -> None:
        self.append(
            SessionEvent(
                type="tool_call",
                tool=tool,
                arguments=arguments,
                tool_call_id=call_id,
            )
        )

    def add_tool_result(self, tool: str, content: str, call_id: str | None = None) -> None:
        self.append(
            SessionEvent(
                type="tool_result",
                tool=tool,
                content=content,
                tool_call_id=call_id,
            )
        )

    def reset(self) -> None:
        """Start a fresh transcript, keeping the file in place."""
        self.events = []
        self.store.clear(self.scope)

    def replace_events(self, events: list[SessionEvent]) -> None:
        """Atomically replace the transcript (used by compaction)."""
        self.events = list(events)
        self.store.replace(self.scope, self.events)

    # -- reading ---------------------------------------------------------

    def build_context(self) -> list[ChatMessage]:
        return ContextBuilder(self.estimator, budget=self.budget).build(self.events)

    @property
    def last_trimmed(self) -> int:
        return len(self.events)

    def message_count(self) -> int:
        return sum(1 for event in self.events if event.type == "message")
