"""Runtime-side MUC room control and persistence.

The runtime must be able to join and leave rooms at runtime (``/join``,
``/leave``) without importing Slixmpp. It depends on the :class:`RoomController`
protocol, which the application layer implements on top of the transport.

Runtime-added rooms are persisted to ``<state>/rooms.json`` so they survive
restarts.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Protocol

from agent.config.models import RoomConfig

log = logging.getLogger(__name__)


class RoomController(Protocol):
    """Join/leave MUC rooms and list the currently active ones."""

    async def join_room(self, room: RoomConfig) -> None: ...

    async def leave_room(self, room_jid: str) -> None: ...

    def set_room_mode(self, room_jid: str, mode: str) -> None: ...

    def list_rooms(self) -> list[RoomConfig]: ...


class RoomStore:
    """Persist runtime-added rooms as a JSON list of :class:`RoomConfig`."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._rooms: dict[str, RoomConfig] = {}

    def load(self) -> list[RoomConfig]:
        if not self.path.is_file():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            log.warning("Ignoring corrupt room store at %s", self.path)
            return []
        rooms: list[RoomConfig] = []
        for entry in raw if isinstance(raw, list) else []:
            try:
                room = RoomConfig.model_validate(entry)
            except Exception:  # noqa: BLE001 - skip invalid persisted entries
                log.warning("Skipping invalid persisted room: %r", entry)
                continue
            rooms.append(room)
            self._rooms[room.jid.lower()] = room
        return rooms

    def add(self, room: RoomConfig) -> None:
        self._rooms[room.jid.lower()] = room
        self._flush()

    def remove(self, room_jid: str) -> None:
        if self._rooms.pop(room_jid.lower(), None) is not None:
            self._flush()

    def all(self) -> list[RoomConfig]:
        return list(self._rooms.values())

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [room.model_dump() for room in self._rooms.values()]
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
