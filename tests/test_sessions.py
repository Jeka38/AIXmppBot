"""Session persistence, JSONL recovery and isolation (SPEC §15, §16, §57)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.llm.tokenizer import TokenEstimator
from agent.memory.compaction import ContextBuilder
from agent.memory.session import SessionEvent, read_jsonl, read_jsonl_lenient, write_jsonl
from agent.memory.store import FilesystemSessionStore
from agent.runtime.sessions import Session


def test_event_round_trip() -> None:
    event = SessionEvent(type="message", role="user", sender="alice@example.com", content="hi")
    restored = SessionEvent.from_json(event.to_json())
    assert restored.content == "hi"
    assert restored.sender == "alice@example.com"
    assert restored.timestamp


def test_write_and_read_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "history.jsonl"
    events = [
        SessionEvent(type="message", role="user", content="one"),
        SessionEvent(type="message", role="assistant", content="two"),
    ]
    write_jsonl(path, events)
    assert [e.content for e in read_jsonl(path)] == ["one", "two"]


def test_lenient_read_skips_corrupt_lines(tmp_path: Path) -> None:
    path = tmp_path / "history.jsonl"
    path.write_text(
        '{"type": "message", "role": "user", "content": "good"}\n'
        "not json at all\n"
        '{"type": "message", "role": "assistant", "content": "also good"}\n',
        encoding="utf-8",
    )
    events, skipped = read_jsonl_lenient(path)
    assert len(events) == 2
    assert skipped == 1


def test_session_appends_and_persists(layout, session_store: FilesystemSessionStore) -> None:
    estimator = TokenEstimator("test")
    scope = layout.scope_for_user("alice@example.com")
    session = Session.load(scope, session_store, estimator, budget=10000)
    session.add_user("hello", sender="alice@example.com")
    session.add_assistant("hi there")

    reloaded = Session.load(scope, session_store, estimator, budget=10000)
    assert reloaded.message_count() == 2
    assert reloaded.events[0].content == "hello"


def test_sessions_are_isolated(layout, session_store: FilesystemSessionStore) -> None:
    estimator = TokenEstimator("test")
    alice = layout.scope_for_user("alice@example.com")
    bob = layout.scope_for_user("bob@example.com")
    bob_session = Session.load(bob, session_store, estimator, budget=10000)
    bob_session.add_user("bob secret", sender="bob@example.com")

    alice_session = Session.load(alice, session_store, estimator, budget=10000)
    assert alice_session.events == []
    assert "bob secret" not in "\n".join(e.content for e in alice_session.events)


def test_muc_sessions_are_isolated(layout, session_store: FilesystemSessionStore) -> None:
    estimator = TokenEstimator("test")
    room_a = layout.scope_for_room("a@conference.example.com")
    room_b = layout.scope_for_room("b@conference.example.com")
    session_a = Session.load(room_a, session_store, estimator, budget=10000)
    session_a.add_user("room A message", sender="alice@example.com")

    session_b = Session.load(room_b, session_store, estimator, budget=10000)
    assert session_b.events == []


def test_jid_path_traversal_is_neutralised(layout) -> None:
    from agent.errors import StorageError
    from agent.memory.store import jid_to_dirname

    # A resource segment is stripped, leaving the safe bare JID.
    assert jid_to_dirname("alice@example.com/../../etc/passwd") == "alice@example.com"
    # Disallowed characters are replaced.
    assert "*" not in jid_to_dirname("a*b@example.com")
    # A pure traversal token is refused outright (fail closed).
    with pytest.raises(StorageError):
        jid_to_dirname("../..")

    scope = layout.scope_for_user("alice@example.com")
    resolved = (layout.memory_dir / scope).resolve()
    assert resolved.is_relative_to(layout.memory_dir)


def test_context_trims_to_budget(estimator: TokenEstimator) -> None:
    builder = ContextBuilder(estimator, budget=60)
    events = [SessionEvent(type="message", role="user", content="word " * 8) for _ in range(20)]
    messages = builder.build(events)
    assert builder.last_trimmed > 0
    assert estimator.count_messages(messages) <= 60


def test_context_never_drops_the_only_message(estimator: TokenEstimator) -> None:
    # Even when a single message exceeds the budget it must be kept, otherwise
    # the model would see an empty conversation.
    builder = ContextBuilder(estimator, budget=1)
    events = [SessionEvent(type="message", role="user", content="x" * 1000)]
    messages = builder.build(events)
    assert len(messages) == 1


def test_context_keeps_system_messages(estimator: TokenEstimator) -> None:
    builder = ContextBuilder(estimator, budget=40)
    events = [
        SessionEvent(type="message", role="system", content="policy"),
        *[SessionEvent(type="message", role="user", content="y" * 300) for _ in range(10)],
    ]
    messages = builder.build(events)
    assert any(m.role == "system" for m in messages)
