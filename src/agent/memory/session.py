"""Session event model and JSONL (de)serialisation.

Every event is a single JSON object per line (SPEC §16). Unknown fields are
preserved on round-trip to stay forward-compatible.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from agent.errors import StorageError

EventType = Literal["message", "tool_call", "tool_result", "summary", "system"]


def utc_now_iso() -> str:
    """Current UTC timestamp in RFC 3339 / XMPP-friendly form."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class SessionEvent(BaseModel):
    """A persisted conversation event."""

    type: EventType
    timestamp: str = Field(default_factory=utc_now_iso)

    # message
    role: str | None = None
    sender: str | None = None
    content: str = ""
    message_id: str | None = None

    # tool_call / tool_result
    tool: str | None = None
    arguments: dict[str, Any] | None = None
    tool_call_id: str | None = None

    # summary
    compacted_count: int | None = None

    model_config = {"extra": "allow"}

    def to_json(self) -> str:
        return json.dumps(self.model_dump(exclude_none=True), ensure_ascii=False)

    @classmethod
    def from_json(cls, line: str) -> SessionEvent:
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise StorageError(f"Corrupt session event: {line[:120]!r}") from exc
        if not isinstance(data, dict):
            raise StorageError("Session event must be a JSON object")
        return cls.model_validate(data)


def read_jsonl(path: Path) -> list[SessionEvent]:
    """Read a JSONL file, skipping blank lines.

    A corrupt line raises :class:`StorageError`; callers that need resilience
    should use :func:`read_jsonl_lenient`.
    """
    if not path.is_file():
        return []
    events: list[SessionEvent] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise StorageError(f"Could not read session file {path}: {exc}") from exc
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            events.append(SessionEvent.from_json(stripped))
    return events


def read_jsonl_lenient(path: Path) -> tuple[list[SessionEvent], int]:
    """Like :func:`read_jsonl` but skips corrupt lines.

    Returns ``(events, skipped_count)`` so the caller can log data loss without
    aborting the whole session.
    """
    if not path.is_file():
        return [], 0
    events: list[SessionEvent] = []
    skipped = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            events.append(SessionEvent.from_json(stripped))
        except StorageError:
            skipped += 1
    return events, skipped


def write_jsonl(path: Path, events: list[SessionEvent]) -> None:
    """Atomically rewrite a JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    body = "\n".join(event.to_json() for event in events)
    if body:
        body += "\n"
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path: Path, event: SessionEvent) -> None:
    """Append one event, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(event.to_json())
        handle.write("\n")
        handle.flush()
