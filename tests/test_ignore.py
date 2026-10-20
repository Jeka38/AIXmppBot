"""Ignore list: global JIDs/domains, per-room nicks, and /ignore commands."""

from __future__ import annotations

from pathlib import Path

from agent.config.models import RoomConfig, SecurityConfig
from agent.i18n import Translator
from agent.llm.tokenizer import TokenEstimator
from agent.memory.store import DataLayout, FilesystemSessionStore
from agent.runtime.commands import CommandContext, CommandHandler
from agent.runtime.sessions import Session
from agent.security.authorization import Authorizer
from agent.security.ignore_store import IgnoreStore, classify_target
from tests.conftest import make_config

ROOM = "room@conference.example.com"
OTHER = "other@conference.example.com"


# -- classification ----------------------------------------------------------


def test_classify_target() -> None:
    assert classify_target("alice@example.com") == ("jid", "alice@example.com")
    assert classify_target("Alice@Example.com/Phone") == ("jid", "alice@example.com")
    assert classify_target("evil.com") == ("domain", "evil.com")
    assert classify_target("nick:Flooder") == ("nick", "flooder")


def test_classify_target_in_room_prefers_nick() -> None:
    # Inside a room a bare word (or @Name) is a nick, not a domain/JID.
    assert classify_target("Flooder", in_room=True) == ("nick", "flooder")
    assert classify_target("@Flooder", in_room=True) == ("nick", "flooder")
    assert classify_target("nick:Flooder", in_room=True) == ("nick", "flooder")
    # An explicit JID stays a JID even in a room.
    assert classify_target("user@example.com", in_room=True) == ("jid", "user@example.com")
    # Without room context a bare word is still a domain.
    assert classify_target("Flooder") == ("domain", "flooder")


# -- global JID/domain -------------------------------------------------------


def test_jid_global_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "ignored.json"
    store = IgnoreStore(path)
    assert store.add_jid("Spammer@Example.com/Res") is True
    assert store.add_jid("spammer@example.com") is False
    assert store.add_domain("evil.com") is True

    reloaded = IgnoreStore(path).load()
    assert reloaded.jids == {"spammer@example.com"}
    assert reloaded.domains == {"evil.com"}
    assert reloaded.is_ignored_jid("spammer@example.com")
    assert reloaded.is_ignored_jid("x@evil.com")
    assert not reloaded.is_ignored_jid("ok@good.com")


def test_jid_ignored_everywhere(tmp_path: Path) -> None:
    store = IgnoreStore(tmp_path / "i.json")
    store.add_jid("spammer@example.com")
    auth = Authorizer(SecurityConfig(allowed_domains=["example.com"]), ignore_store=store)
    assert not auth.check("spammer@example.com").allowed
    room = RoomConfig(jid=ROOM)
    assert not auth.check_room(room, "spammer@example.com").allowed


# -- per-room nicks ----------------------------------------------------------


def test_nick_is_per_room(tmp_path: Path) -> None:
    path = tmp_path / "ignored.json"
    store = IgnoreStore(path)
    assert store.add_nick(ROOM, "Flooder") is True
    assert store.add_nick(ROOM, "flooder") is False  # case-insensitive, duplicate

    reloaded = IgnoreStore(path).load()
    assert reloaded.is_ignored_nick(ROOM, "Flooder")
    # Not ignored in another room.
    assert not reloaded.is_ignored_nick(OTHER, "Flooder")
    assert reloaded.room_nicks(ROOM) == ["flooder"]
    assert reloaded.room_nicks(OTHER) == []


def test_room_authorizer_uses_nick_scope(tmp_path: Path) -> None:
    store = IgnoreStore(tmp_path / "i.json")
    store.add_nick(ROOM, "Flooder")
    auth = Authorizer(SecurityConfig(allowed_domains=["example.com"]), ignore_store=store)
    room = RoomConfig(jid=ROOM)
    other = RoomConfig(jid=OTHER)
    # Denied in the room where the nick is banned...
    assert not auth.check_room(room, f"{ROOM}/Flooder", nick="Flooder").allowed
    # ...but allowed in another room.
    assert auth.check_room(other, f"{OTHER}/Flooder", nick="Flooder").allowed


def test_remove_nick(tmp_path: Path) -> None:
    store = IgnoreStore(tmp_path / "i.json")
    store.add_nick(ROOM, "Flooder")
    assert store.remove_nick(ROOM, "Flooder") is True
    assert store.remove_nick(ROOM, "Flooder") is False
    assert IgnoreStore(tmp_path / "i.json").load().room_nicks(ROOM) == []


def test_load_legacy_scoped_and_corrupt(tmp_path: Path) -> None:
    path = tmp_path / "ignored.json"
    path.write_text('{"jids": ["a@b.com"], "rooms": {"room@x": ["Nick"]}}', encoding="utf-8")
    store = IgnoreStore(path).load()
    assert store.is_ignored_jid("a@b.com")
    assert store.is_ignored_nick("room@x", "nick")

    path.write_text("not json", encoding="utf-8")
    assert IgnoreStore(path).load().jids == set()


# -- commands ----------------------------------------------------------------


def _context(
    tmp_path: Path, *, is_dev: bool, room: RoomConfig | None = None, affiliation: str = ""
) -> tuple[CommandHandler, CommandContext]:
    config = make_config()
    layout = DataLayout(tmp_path / "data")
    session = Session.load(
        layout.scope_for_user("alice@example.com"),
        FilesystemSessionStore(layout.memory_dir),
        TokenEstimator("test"),
        budget=1000,
    )
    context = CommandContext(
        session=session,
        config=config,
        jid="admin@example.com" if is_dev else "alice@example.com",
        is_dev=is_dev,
        translator=Translator("en"),
        ignore_store=IgnoreStore(tmp_path / "ignored.json"),
        current_room=room,
        affiliation=affiliation,
    )
    return CommandHandler(config, translator=Translator("en")), context


def test_admin_ignores_jid_in_dm(tmp_path: Path) -> None:
    handler, context = _context(tmp_path, is_dev=True)
    result = handler.dispatch("/ignore spammer@example.com", context)
    assert result is not None and "Ignoring" in result.reply
    assert context.ignore_store is not None
    assert "spammer@example.com" in context.ignore_store.jids


def test_non_admin_cannot_ignore_jid(tmp_path: Path) -> None:
    handler, context = _context(tmp_path, is_dev=False)
    result = handler.dispatch("/ignore spammer@example.com", context)
    assert result is not None and "administrator" in result.reply.lower()


def test_room_owner_ignores_nick(tmp_path: Path) -> None:
    handler, context = _context(
        tmp_path, is_dev=False, room=RoomConfig(jid=ROOM, nick="Bot"), affiliation="owner"
    )
    result = handler.dispatch("/ignore nick:Flooder", context)
    assert result is not None and "Ignoring" in result.reply
    assert context.ignore_store is not None
    assert context.ignore_store.room_nicks(ROOM) == ["flooder"]


def test_room_owner_ignores_bare_nick(tmp_path: Path) -> None:
    """A bare word inside a room is a nick, not a domain (regression)."""
    handler, context = _context(
        tmp_path, is_dev=False, room=RoomConfig(jid=ROOM, nick="Bot"), affiliation="owner"
    )
    result = handler.dispatch("/ignore Flooder", context)
    assert result is not None and "Ignoring" in result.reply
    assert context.ignore_store is not None
    assert context.ignore_store.room_nicks(ROOM) == ["flooder"]


def test_room_owner_ignores_at_nick(tmp_path: Path) -> None:
    handler, context = _context(
        tmp_path, is_dev=False, room=RoomConfig(jid=ROOM, nick="Bot"), affiliation="owner"
    )
    result = handler.dispatch("/ignore @Flooder", context)
    assert result is not None and "Ignoring" in result.reply
    assert context.ignore_store is not None
    assert context.ignore_store.room_nicks(ROOM) == ["flooder"]


def test_room_non_owner_cannot_ignore_nick(tmp_path: Path) -> None:
    handler, context = _context(
        tmp_path, is_dev=False, room=RoomConfig(jid=ROOM, nick="Bot"), affiliation="member"
    )
    result = handler.dispatch("/ignore nick:Flooder", context)
    assert result is not None and "owner" in result.reply.lower()
    assert context.ignore_store is not None
    assert context.ignore_store.room_nicks(ROOM) == []


def test_nick_ignore_outside_room_rejected(tmp_path: Path) -> None:
    handler, context = _context(tmp_path, is_dev=True)  # admin, but no room
    result = handler.dispatch("/ignore nick:Flooder", context)
    assert result is not None and "room" in result.reply.lower()


def test_ignore_list_room_and_global(tmp_path: Path) -> None:
    handler, context = _context(
        tmp_path, is_dev=True, room=RoomConfig(jid=ROOM, nick="Bot"), affiliation="owner"
    )
    handler.dispatch("/ignore nick:Flooder", context)
    handler.dispatch("/ignore spammer@example.com", context)
    result = handler.dispatch("/ignore", context)
    assert result is not None
    assert "flooder" in result.reply
    assert "spammer@example.com" in result.reply


def test_unignore_nick(tmp_path: Path) -> None:
    handler, context = _context(
        tmp_path, is_dev=False, room=RoomConfig(jid=ROOM, nick="Bot"), affiliation="owner"
    )
    handler.dispatch("/ignore nick:Flooder", context)
    result = handler.dispatch("/unignore nick:Flooder", context)
    assert result is not None and "No longer" in result.reply
    assert context.ignore_store is not None
    assert context.ignore_store.room_nicks(ROOM) == []
