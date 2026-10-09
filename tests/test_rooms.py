"""Admin-only room management: /join, /leave, /rooms and persistence."""

from __future__ import annotations

from pathlib import Path

from agent.config.models import RoomConfig
from agent.i18n import Translator
from agent.llm.tokenizer import TokenEstimator
from agent.memory.store import DataLayout, FilesystemSessionStore
from agent.runtime.commands import CommandContext, CommandHandler
from agent.runtime.rooms import RoomStore
from agent.runtime.sessions import Session
from tests.conftest import make_config


class FakeRoomController:
    def __init__(self) -> None:
        self._rooms: dict[str, RoomConfig] = {}
        self.joined: list[RoomConfig] = []
        self.left: list[str] = []
        self.modes: list[tuple[str, str]] = []

    async def join_room(self, room: RoomConfig) -> None:
        self.joined.append(room)
        self._rooms[room.jid.lower()] = room

    async def leave_room(self, room_jid: str) -> None:
        self.left.append(room_jid)
        self._rooms.pop(room_jid.lower(), None)

    def set_room_mode(self, room_jid: str, mode: str) -> None:
        self.modes.append((room_jid, mode))
        room = self._rooms.get(room_jid.lower())
        if room is not None:
            self._rooms[room_jid.lower()] = room.model_copy(update={"activation": mode})

    def list_rooms(self) -> list[RoomConfig]:
        return list(self._rooms.values())


def _setup(tmp_path: Path, *, is_dev: bool):
    config = make_config()
    layout = DataLayout(tmp_path / "data")
    store = FilesystemSessionStore(layout.memory_dir)
    session = Session.load(
        layout.scope_for_user("alice@example.com"),
        store,
        TokenEstimator("test"),
        budget=1000,
    )
    controller = FakeRoomController()
    context = CommandContext(
        session=session,
        config=config,
        jid="admin@example.com",
        is_dev=is_dev,
        translator=Translator("en"),
        rooms=controller,
    )
    return CommandHandler(config, translator=Translator("en")), context, controller


def test_join_is_admin_only(tmp_path: Path) -> None:
    handler, context, _ = _setup(tmp_path, is_dev=False)
    result = handler.dispatch("/join room@conference.example.com", context)
    assert result is not None
    assert "administrators only" in result.reply
    assert result.join_room is None


def test_join_parses_room_and_nick(tmp_path: Path) -> None:
    handler, context, _ = _setup(tmp_path, is_dev=True)
    result = handler.dispatch("/join room@conference.example.com MyBot", context)
    assert result is not None
    assert result.join_room is not None
    assert result.join_room.jid == "room@conference.example.com"
    assert result.join_room.nick == "MyBot"
    assert result.join_room.activation == "mention"


def test_join_activation_argument(tmp_path: Path) -> None:
    handler, context, _ = _setup(tmp_path, is_dev=True)
    result = handler.dispatch("/join room@conference.example.com MyBot all", context)
    assert result is not None
    assert result.join_room is not None
    assert result.join_room.activation == "all"


def test_join_requires_argument(tmp_path: Path) -> None:
    handler, context, _ = _setup(tmp_path, is_dev=True)
    result = handler.dispatch("/join", context)
    assert result is not None
    assert "Usage" in result.reply


def test_join_rejects_non_jid(tmp_path: Path) -> None:
    handler, context, _ = _setup(tmp_path, is_dev=True)
    result = handler.dispatch("/join not-a-jid", context)
    assert result is not None
    assert result.join_room is None


def test_leave_requires_argument_or_current_room(tmp_path: Path) -> None:
    handler, context, _ = _setup(tmp_path, is_dev=True)
    result = handler.dispatch("/leave", context)
    assert result is not None
    assert "Usage" in result.reply


def test_rooms_lists_active(tmp_path: Path) -> None:
    handler, context, controller = _setup(tmp_path, is_dev=True)
    controller._rooms["room@conference.example.com"] = RoomConfig(  # noqa: SLF001
        jid="room@conference.example.com", nick="Bot"
    )
    result = handler.dispatch("/rooms", context)
    assert result is not None
    assert "room@conference.example.com" in result.reply


def test_rooms_admin_only(tmp_path: Path) -> None:
    handler, context, _ = _setup(tmp_path, is_dev=False)
    result = handler.dispatch("/rooms", context)
    assert result is not None
    assert "administrators only" in result.reply


# -- /mode (room owner only) -------------------------------------------------


def _room_context(tmp_path: Path, *, affiliation: str, activation: str = "mention"):
    config = make_config()
    layout = DataLayout(tmp_path / "data")
    store = FilesystemSessionStore(layout.memory_dir)
    session = Session.load(
        layout.scope_for_user("alice@example.com"),
        store,
        TokenEstimator("test"),
        budget=1000,
    )
    controller = FakeRoomController()
    room = RoomConfig(jid="room@conference.example.com", nick="Bot", activation=activation)
    controller._rooms[room.jid.lower()] = room  # noqa: SLF001
    context = CommandContext(
        session=session,
        config=config,
        jid="room@conference.example.com/Alice",
        is_dev=False,
        translator=Translator("en"),
        rooms=controller,
        current_room=room,
        affiliation=affiliation,
    )
    return CommandHandler(config, translator=Translator("en")), context, controller


def test_mode_owner_can_set(tmp_path: Path) -> None:
    handler, context, controller = _room_context(tmp_path, affiliation="owner")
    result = handler.dispatch("/mode all", context)
    assert result is not None
    assert result.set_room_mode == ("room@conference.example.com", "all")
    assert "set to 'all'" in result.reply


def test_mode_non_owner_denied(tmp_path: Path) -> None:
    handler, context, _ = _room_context(tmp_path, affiliation="member")
    result = handler.dispatch("/mode all", context)
    assert result is not None
    assert result.set_room_mode is None
    assert "owner" in result.reply.lower()


def test_mode_anonymous_owner_denied_without_affiliation(tmp_path: Path) -> None:
    # Anonymous senders still expose affiliation; empty means we cannot verify.
    handler, context, _ = _room_context(tmp_path, affiliation="")
    result = handler.dispatch("/mode all", context)
    assert result is not None
    assert result.set_room_mode is None


def test_mode_reports_current_without_args(tmp_path: Path) -> None:
    handler, context, _ = _room_context(tmp_path, affiliation="owner", activation="all")
    result = handler.dispatch("/mode", context)
    assert result is not None
    assert "all" in result.reply


def test_mode_invalid_value(tmp_path: Path) -> None:
    handler, context, _ = _room_context(tmp_path, affiliation="owner")
    result = handler.dispatch("/mode sometimes", context)
    assert result is not None
    assert result.set_room_mode is None
    assert "Unknown mode" in result.reply


def test_mode_requires_room_context(tmp_path: Path) -> None:
    handler, context, _ = _room_context(tmp_path, affiliation="owner")
    context.current_room = None
    result = handler.dispatch("/mode all", context)
    assert result is not None
    assert "inside a room" in result.reply


# -- persistence -------------------------------------------------------------


def test_room_store_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "state" / "rooms.json"
    store = RoomStore(path)
    store.add(RoomConfig(jid="room@conference.example.com", nick="Bot", activation="all"))
    assert path.is_file()

    reloaded = RoomStore(path).load()
    assert len(reloaded) == 1
    assert reloaded[0].jid == "room@conference.example.com"
    assert reloaded[0].activation == "all"


def test_room_store_remove(tmp_path: Path) -> None:
    path = tmp_path / "rooms.json"
    store = RoomStore(path)
    store.add(RoomConfig(jid="r@conference.example.com", nick="Bot"))
    store.remove("r@conference.example.com")
    assert RoomStore(path).load() == []


def test_room_store_ignores_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "rooms.json"
    path.write_text("not json", encoding="utf-8")
    assert RoomStore(path).load() == []
