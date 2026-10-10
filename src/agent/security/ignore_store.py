"""Persistent ignore-list, scoped per chat.

Each chat (a DM user or a MUC room) can have its own ignore list, managed by a
bot administrator in DMs and by the room owner inside a room. A ``global`` scope
applies everywhere. Entries added from the config
(``security.ignored_jids`` / ``ignored_nicks``) are merged in by
:class:`~agent.security.authorization.Authorizer` and are always global.

Stored as ``<state>/ignored.json``::

    {
      "global": {"jids": [], "domains": [], "nicks": []},
      "rooms": {
        "room@conference.example.com": {"jids": [], "domains": [], "nicks": ["Flooder"]}
      }
    }
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

IgnoreKind = Literal["jid", "domain", "nick"]
GLOBAL_SCOPE = "global"


def scope_for_room(room_jid: str) -> str:
    return f"room:{room_jid.strip().lower()}"


@dataclass
class ScopeEntries:
    jids: set[str] = field(default_factory=set)
    domains: set[str] = field(default_factory=set)
    nicks: set[str] = field(default_factory=set)

    def empty(self) -> bool:
        return not (self.jids or self.domains or self.nicks)

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "jids": sorted(self.jids),
            "domains": sorted(self.domains),
            "nicks": sorted(self.nicks),
        }


@dataclass
class IgnoreStore:
    """Mutable, persistent, per-scope ignore-list."""

    path: Path
    scopes: dict[str, ScopeEntries] = field(default_factory=dict)

    # -- access ----------------------------------------------------------

    def _scope(self, scope: str) -> ScopeEntries:
        return self.scopes.setdefault(scope, ScopeEntries())

    def entries(self, scope: str = GLOBAL_SCOPE) -> list[tuple[IgnoreKind, str]]:
        entries = self.scopes.get(scope, ScopeEntries())
        out: list[tuple[IgnoreKind, str]] = []
        out.extend(("jid", v) for v in sorted(entries.jids))
        out.extend(("domain", v) for v in sorted(entries.domains))
        out.extend(("nick", v) for v in sorted(entries.nicks))
        return out

    # -- persistence -----------------------------------------------------

    def load(self) -> IgnoreStore:
        if not self.path.is_file():
            return self
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            log.warning("Ignoring corrupt ignore store at %s", self.path)
            return self
        if not isinstance(raw, dict):
            return self

        # Backward compatibility with the old flat format.
        if any(key in raw for key in ("jids", "domains", "nicks")):
            self.scopes[GLOBAL_SCOPE] = _entries_from(raw)
            return self

        global_block = raw.get("global")
        if isinstance(global_block, dict):
            self.scopes[GLOBAL_SCOPE] = _entries_from(global_block)
        rooms = raw.get("rooms")
        if isinstance(rooms, dict):
            for room_jid, block in rooms.items():
                if isinstance(block, dict):
                    self.scopes[scope_for_room(str(room_jid))] = _entries_from(block)
        return self

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rooms: dict[str, dict[str, list[str]]] = {}
        for scope, entries in self.scopes.items():
            if scope == GLOBAL_SCOPE or entries.empty():
                continue
            room = scope[5:] if scope.startswith("room:") else scope
            rooms[room] = entries.to_dict()
        payload = {
            "global": self.scopes.get(GLOBAL_SCOPE, ScopeEntries()).to_dict(),
            "rooms": rooms,
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # -- mutation --------------------------------------------------------

    def add(self, kind: IgnoreKind, value: str, *, scope: str = GLOBAL_SCOPE) -> bool:
        """Add an entry; returns ``False`` if it was already present."""
        value = value.strip().lower()
        if not value:
            return False
        target = self._target(self._scope(scope), kind)
        if value in target:
            return False
        target.add(value)
        self._flush()
        return True

    def remove(self, kind: IgnoreKind, value: str, *, scope: str = GLOBAL_SCOPE) -> bool:
        value = value.strip().lower()
        target = self._target(self._scope(scope), kind)
        if value not in target:
            return False
        target.discard(value)
        self._flush()
        return True

    @staticmethod
    def _target(entries: ScopeEntries, kind: IgnoreKind) -> set[str]:
        return {"jid": entries.jids, "domain": entries.domains, "nick": entries.nicks}[kind]

    # -- matching --------------------------------------------------------

    def matches(self, scope: str, *, jid: str = "", nick: str = "") -> bool:
        """Whether *jid*/*nick* is ignored in *scope* or globally."""
        jid = jid.strip().lower()
        nick = nick.strip().lower()
        for scope_key in (scope, GLOBAL_SCOPE):
            entries = self.scopes.get(scope_key)
            if entries is None:
                continue
            if nick and nick in entries.nicks:
                return True
            if jid and jid in entries.jids:
                return True
            if jid and "@" in jid and jid.rsplit("@", 1)[1] in entries.domains:
                return True
        return False


def _entries_from(block: dict[str, object]) -> ScopeEntries:
    def clean(key: str) -> set[str]:
        raw = block.get(key, [])
        if not isinstance(raw, (list, tuple, set)):
            return set()
        return {str(v).strip().lower() for v in raw if str(v).strip()}

    return ScopeEntries(jids=clean("jids"), domains=clean("domains"), nicks=clean("nicks"))


def classify_target(raw: str) -> tuple[IgnoreKind, str]:
    """Infer the kind of an ``/ignore`` target.

    ``nick:Name`` → nick, anything with ``@`` → JID, otherwise a domain.
    """
    value = raw.strip()
    if value.lower().startswith("nick:"):
        return "nick", value[5:].strip().lower()
    if "@" in value:
        return "jid", value.split("/", 1)[0].strip().lower()
    return "domain", value.lower()
