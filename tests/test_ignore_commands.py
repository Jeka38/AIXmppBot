"""Runtime ignore-list: persistence, classification and /ignore commands."""

from __future__ import annotations

from pathlib import Path

from agent.config.models import SecurityConfig
from agent.i18n import Translator
from agent.llm.tokenizer import TokenEstimator
from agent.memory.store import DataLayout, FilesystemSessionStore
from agent.runtime.commands import CommandContext, CommandHandler
from agent.runtime.sessions import Session
from agent.security.authorization import Authorizer
from agent.security.ignore_store import (
    GLOBAL_SCOPE,
    IgnoreStore,
    classify_target,
    scope_for_room,
)
from tests.conftest import make_config

# -- classification ----------------------------------------------------------


def test_classify_target() -> None:
    assert classify_target("alice@example.com") == ("jid", "alice@example.com")
    assert classify_target("Alice@Example.com/Phone") == ("jid", "alice@example.com")
    assert classify_target("evil.com") == ("domain", "evil.com")
    assert classify_target("nick:Flooder") == ("nick", "flooder")
    assert classify_target("NICK:Bob") == ("nick", "bob")


# -- store -------------------------------------------------------------------


def test_store_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "ignored.json"
    store = IgnoreStore(path)
    assert store.add("jid", "Spammer@Example.com") is True
    assert store.add("domain", "evil.com") is True
    assert store.add("nick", "Flooder") is True
    assert store.add("jid", "spammer@example.com") is False  # duplicate

    reloaded = IgnoreStore(path).load()
    entries = dict(reloaded.entries())
    assert entries["jid"] == "spammer@example.com"
    assert {v for k, v in reloaded.entries() if k == "domain"} == {"evil.com"}
    assert {v for k, v in reloaded.entries() if k == "nick"} == {"flooder"}


def test_store_per_scope(tmp_path: Path) -> None:
    path = tmp_path / "ignored.json"
    store = IgnoreStore(path)
    store.add("nick", "Flooder", scope=scope_for_room("room@conference.example.com"))
    store.add("jid", "spammer@example.com")  # global

    reloaded = IgnoreStore(path).load()
    room_entries = reloaded.entries(scope_for_room("room@conference.example.com"))
    assert ("nick", "flooder") in room_entries
    assert ("jid", "spammer@example.com") in reloaded.entries(GLOBAL_SCOPE)
    # The room ignore does not leak globally.
    assert not reloaded.matches(GLOBAL_SCOPE, nick="flooder")
    assert reloaded.matches(scope_for_room("room@conference.example.com"), nick="flooder")


def test_store_remove(tmp_path: Path) -> None:
    store = IgnoreStore(tmp_path / "ignored.json")
    store.add("domain", "evil.com")
    assert store.remove("domain", "evil.com") is True
    assert store.remove("domain", "evil.com") is False
    assert IgnoreStore(tmp_path / "ignored.json").load().entries() == []


def test_store_loads_legacy_flat_format(tmp_path: Path) -> None:
    path = tmp_path / "ignored.json"
    path.write_text('{"jids": ["a@b.com"], "domains": [], "nicks": []}', encoding="utf-8")
    store = IgnoreStore(path).load()
    assert ("jid", "a@b.com") in store.entries(GLOBAL_SCOPE)


def test_store_ignores_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "ignored.json"
    path.write_text("not json", encoding="utf-8")
    assert IgnoreStore(path).load().entries() == []


# -- authorizer merges config + store ----------------------------------------


def test_runtime_ignore_affects_authorizer(tmp_path: Path) -> None:
    store = IgnoreStore(tmp_path / "ignored.json")
    auth = Authorizer(
        SecurityConfig(allowed_jids=["alice@example.com"], ignored_jids=[]),
        ignore_store=store,
    )
    assert auth.is_authorized("alice@example.com")
    store.add("jid", "alice@example.com")
    # Now ignored at runtime, even though still allowlisted.
    assert not auth.is_authorized("alice@example.com")


def test_runtime_nick_ignore(tmp_path: Path) -> None:
    store = IgnoreStore(tmp_path / "ignored.json")
    auth = Authorizer(SecurityConfig(allowed_domains=["example.com"]), ignore_store=store)
    assert not auth.is_ignored("room@conference.example.com/x", nick="Flooder")
    store.add("nick", "Flooder")
    assert auth.is_ignored("room@conference.example.com/x", nick="Flooder")


# -- commands ----------------------------------------------------------------


def _setup(tmp_path: Path, *, is_dev: bool, store: IgnoreStore | None = None):
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
        ignore_store=store if store is not None else IgnoreStore(tmp_path / "i.json"),
    )
    return CommandHandler(config, translator=Translator("en")), context


def test_ignore_denied_for_non_owner(tmp_path: Path) -> None:
    handler, context = _setup(tmp_path, is_dev=False)
    result = handler.dispatch("/ignore spammer@example.com", context)
    assert result is not None and "owner" in result.reply.lower()


def test_ignore_adds_jid(tmp_path: Path) -> None:
    handler, context = _setup(tmp_path, is_dev=True)
    result = handler.dispatch("/ignore spammer@example.com", context)
    assert result is not None and "Ignoring" in result.reply
    assert context.ignore_store is not None
    assert ("jid", "spammer@example.com") in context.ignore_store.entries(GLOBAL_SCOPE)


def test_ignore_adds_domain_and_nick(tmp_path: Path) -> None:
    handler, context = _setup(tmp_path, is_dev=True)
    handler.dispatch("/ignore evil.com", context)
    handler.dispatch("/ignore nick:Flooder", context)
    store = context.ignore_store
    assert store is not None
    entries = store.entries(GLOBAL_SCOPE)
    assert ("domain", "evil.com") in entries
    assert ("nick", "flooder") in entries


def test_ignore_room_owner_allowed_and_scoped(tmp_path: Path) -> None:
    from agent.config.models import RoomConfig

    handler, context = _setup(tmp_path, is_dev=False)
    context.current_room = RoomConfig(jid="room@conference.example.com", nick="Bot")
    context.affiliation = "owner"
    context.ignore_scope = scope_for_room("room@conference.example.com")

    result = handler.dispatch("/ignore nick:Flooder", context)
    assert result is not None and "Ignoring" in result.reply
    store = context.ignore_store
    assert store is not None
    # Stored under the room scope, not globally.
    assert ("nick", "flooder") in store.entries(scope_for_room("room@conference.example.com"))
    assert ("nick", "flooder") not in store.entries(GLOBAL_SCOPE)


def test_ignore_list_shows_entries(tmp_path: Path) -> None:
    handler, context = _setup(tmp_path, is_dev=True)
    handler.dispatch("/ignore evil.com", context)
    result = handler.dispatch("/ignore", context)
    assert result is not None
    assert "evil.com" in result.reply


def test_unignore_removes(tmp_path: Path) -> None:
    handler, context = _setup(tmp_path, is_dev=True)
    handler.dispatch("/ignore evil.com", context)
    result = handler.dispatch("/unignore evil.com", context)
    assert result is not None and "No longer" in result.reply
    store = context.ignore_store
    assert store is not None
    assert ("domain", "evil.com") not in store.entries(GLOBAL_SCOPE)


def test_ignore_without_store_unavailable(tmp_path: Path) -> None:
    handler, context = _setup(tmp_path, is_dev=True)
    context.ignore_store = None
    result = handler.dispatch("/ignore spammer@example.com", context)
    assert result is not None and "not available" in result.reply
