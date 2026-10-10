"""Persistent ignore-list.

Two independent rules, matching how XMPP exposes identity:

* **JIDs and domains are global.** A JID is visible everywhere, so ignoring one
  applies to DMs and to every room.
* **Nicks are per-room.** In public (semi-anonymous) MUCs the real JID is hidden,
  so a nick can only be trusted within the room it was banned in. Each room keeps
  its own nick list and a ban in one room never affects another.

Stored as ``<state>/ignored.json``::

    {
      "jids": ["spammer@example.com"],
      "domains": ["evil.com"],
      "rooms": {
        "room@conference.example.com": ["flooder", "badnick"]
      }
    }

Managed with ``/ignore`` and ``/unignore``: bot administrators manage the global
part; a room owner manages their room's nick list.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

IgnoreKind = Literal["jid", "domain", "nick"]


@dataclass
class IgnoreStore:
    """Mutable, persistent ignore-list (global JIDs/domains + per-room nicks)."""

    path: Path
    jids: set[str] = field(default_factory=set)
    domains: set[str] = field(default_factory=set)
    rooms: dict[str, set[str]] = field(default_factory=dict)

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
        self.jids = _clean_set(raw.get("jids"))
        self.domains = _clean_set(raw.get("domains"))
        rooms = raw.get("rooms", {})
        if isinstance(rooms, dict):
            for room_jid, nicks in rooms.items():
                cleaned = _clean_set(nicks)
                if cleaned:
                    self.rooms[str(room_jid).strip().lower()] = cleaned
        return self

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "jids": sorted(self.jids),
            "domains": sorted(self.domains),
            "rooms": {room: sorted(nicks) for room, nicks in sorted(self.rooms.items()) if nicks},
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # -- global JIDs / domains -------------------------------------------

    def add_jid(self, value: str) -> bool:
        return self._add(self.jids, value.strip().lower().split("/", 1)[0])

    def add_domain(self, value: str) -> bool:
        return self._add(self.domains, value.strip().lower())

    def remove_jid(self, value: str) -> bool:
        return self._remove(self.jids, value.strip().lower().split("/", 1)[0])

    def remove_domain(self, value: str) -> bool:
        return self._remove(self.domains, value.strip().lower())

    # -- per-room nicks --------------------------------------------------

    def add_nick(self, room_jid: str, nick: str) -> bool:
        room = room_jid.strip().lower()
        value = nick.strip().lower()
        if not room or not value:
            return False
        nicks = self.rooms.setdefault(room, set())
        if value in nicks:
            return False
        nicks.add(value)
        self._flush()
        return True

    def remove_nick(self, room_jid: str, nick: str) -> bool:
        room = room_jid.strip().lower()
        value = nick.strip().lower()
        nicks = self.rooms.get(room)
        if not nicks or value not in nicks:
            return False
        nicks.discard(value)
        if not nicks:
            self.rooms.pop(room, None)
        self._flush()
        return True

    def room_nicks(self, room_jid: str) -> list[str]:
        return sorted(self.rooms.get(room_jid.strip().lower(), set()))

    # -- matching --------------------------------------------------------

    def is_ignored_jid(self, jid: str) -> bool:
        bare = jid.strip().lower().split("/", 1)[0]
        if not bare:
            return False
        if bare in self.jids:
            return True
        if "@" in bare and bare.rsplit("@", 1)[1] in self.domains:
            return True
        return False

    def is_ignored_nick(self, room_jid: str, nick: str) -> bool:
        room = room_jid.strip().lower()
        return nick.strip().lower() in self.rooms.get(room, set())

    # -- helpers ---------------------------------------------------------

    def _add(self, target: set[str], value: str) -> bool:
        if not value or value in target:
            return False
        target.add(value)
        self._flush()
        return True

    def _remove(self, target: set[str], value: str) -> bool:
        if value not in target:
            return False
        target.discard(value)
        self._flush()
        return True


def _clean_set(raw: object) -> set[str]:
    if not isinstance(raw, (list, tuple, set)):
        return set()
    return {str(v).strip().lower() for v in raw if str(v).strip()}


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
