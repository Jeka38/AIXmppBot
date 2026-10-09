"""Storage interfaces and filesystem implementations.

All persistence goes through these Protocols (SPEC §46) so the JSONL/Markdown
implementation can later be swapped for PostgreSQL without touching the runtime.

Security: every path derived from a JID or room is sanitised and confined to
the configured data directory. Path traversal is impossible by construction.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

from agent.errors import StorageError
from agent.memory.session import (
    SessionEvent,
    append_jsonl,
    read_jsonl,
    read_jsonl_lenient,
    write_jsonl,
)

_UNSAFE = re.compile(r"[^A-Za-z0-9@._\-]")


def jid_to_dirname(jid: str) -> str:
    """Map a JID to a safe single-segment directory name.

    ``alice@example.com/resource`` becomes ``alice@example.com`` (bare JID),
    and any character outside the allowlist is replaced with ``_``.
    """
    bare = jid.split("/", 1)[0].strip().lower()
    if not bare:
        raise StorageError("Cannot derive a storage path from an empty JID")
    safe = _UNSAFE.sub("_", bare)
    if safe in {".", ".."}:
        raise StorageError(f"Refusing unsafe storage segment for JID {jid!r}")
    return safe


class MemoryStore(Protocol):
    """Long-term human-readable memory (``memory.md`` / ``user.md``)."""

    def read(self, key: str) -> str: ...

    def write(self, key: str, content: str) -> None: ...

    def append(self, key: str, content: str) -> None: ...


class FilesystemMemoryStore:
    """Markdown files under a root directory, addressed by a relative key."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        if not candidate.is_relative_to(self.root):
            raise StorageError(f"Memory key escapes the data directory: {key!r}")
        return candidate

    def read(self, key: str) -> str:
        path = self._resolve(key)
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8")

    def write(self, key: str, content: str) -> None:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)

    def append(self, key: str, content: str) -> None:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(content)
            if not content.endswith("\n"):
                handle.write("\n")


class SessionStore(Protocol):
    """Numbered per-conversation JSONL transcripts."""

    def append(self, scope: str, event: SessionEvent) -> None: ...

    def load(self, scope: str) -> list[SessionEvent]: ...

    def clear(self, scope: str) -> None: ...

    def replace(self, scope: str, events: list[SessionEvent]) -> None: ...


class FilesystemSessionStore:
    """JSONL transcripts beneath ``root/<scope>/sessions/<name>.jsonl``."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, scope: str) -> Path:
        candidate = (self.root / scope).resolve()
        if not candidate.is_relative_to(self.root):
            raise StorageError(f"Session scope escapes the data directory: {scope!r}")
        return candidate

    def append(self, scope: str, event: SessionEvent) -> None:
        append_jsonl(self._dir(scope) / "history.jsonl", event)

    def load(self, scope: str) -> list[SessionEvent]:
        return read_jsonl(self._dir(scope) / "history.jsonl")

    def load_lenient(self, scope: str) -> tuple[list[SessionEvent], int]:
        return read_jsonl_lenient(self._dir(scope) / "history.jsonl")

    def clear(self, scope: str) -> None:
        path = self._dir(scope) / "history.jsonl"
        write_jsonl(path, [])

    def replace(self, scope: str, events: list[SessionEvent]) -> None:
        path = self._dir(scope) / "history.jsonl"
        write_jsonl(path, events)


class DataLayout:
    """Resolves the on-disk locations defined in SPEC §15."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir.resolve()
        self.memory_dir = self.data_dir / "memory"
        self.users_dir = self.memory_dir / "users"
        self.rooms_dir = self.memory_dir / "rooms"
        self.state_dir = self.data_dir / "state"
        self.personas_dir = self.data_dir / "personas"
        self.skills_dir = self.data_dir / "skills"
        for directory in (
            self.users_dir,
            self.rooms_dir,
            self.state_dir,
            self.personas_dir,
            self.skills_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    # -- users -----------------------------------------------------------

    def user_dir(self, jid: str) -> Path:
        return self.users_dir / jid_to_dirname(jid)

    def user_memory(self, jid: str) -> Path:
        return self.user_dir(jid) / "memory.md"

    def user_profile(self, jid: str) -> Path:
        return self.user_dir(jid) / "user.md"

    def user_sessions_dir(self, jid: str) -> Path:
        return self.user_dir(jid) / "sessions"

    def user_personas_dir(self, jid: str) -> Path:
        return self.user_dir(jid) / "personas"

    # -- rooms -----------------------------------------------------------

    def room_dir(self, room_jid: str) -> Path:
        return self.rooms_dir / jid_to_dirname(room_jid)

    def room_memory(self, room_jid: str) -> Path:
        return self.room_dir(room_jid) / "memory.md"

    def scope_for_user(self, jid: str) -> str:
        return f"users/{jid_to_dirname(jid)}"

    def scope_for_room(self, room_jid: str) -> str:
        return f"rooms/{jid_to_dirname(room_jid)}"
