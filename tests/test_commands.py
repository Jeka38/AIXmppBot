"""Slash commands are intercepted before the LLM (SPEC §17)."""

from __future__ import annotations

from agent.llm.tokenizer import TokenEstimator
from agent.runtime.commands import CommandContext, CommandHandler, parse_command
from agent.runtime.sessions import Session


def _context(config, session, *, is_dev=False) -> CommandContext:
    return CommandContext(
        session=session,
        config=config,
        jid="admin@example.com" if is_dev else "alice@example.com",
        is_dev=is_dev,
    )


def _session(layout, store) -> Session:
    return Session.load(
        layout.scope_for_user("alice@example.com"),
        store,
        TokenEstimator("test"),
        budget=1000,
    )


def test_parse_command_extracts_name_and_args() -> None:
    assert parse_command("/new") == ("new", [])
    assert parse_command("/history 50") == ("history", ["50"])
    assert parse_command("hello /new") is None
    assert parse_command("/") is None


def test_user_commands(config, layout, session_store) -> None:
    handler = CommandHandler(config)
    session = _session(layout, session_store)
    for name in ("ping", "help", "status"):
        result = handler.dispatch(f"/{name}", _context(config, session))
        assert result is not None and result.handled


def test_new_session_flag(config, layout, session_store) -> None:
    handler = CommandHandler(config)
    session = _session(layout, session_store)
    result = handler.dispatch("/new", _context(config, session))
    assert result is not None and result.new_session


def test_forget_sets_memory_flag(config, layout, session_store) -> None:
    handler = CommandHandler(config)
    session = _session(layout, session_store)
    result = handler.dispatch("/forget", _context(config, session))
    assert result is not None and result.forget_memory and result.new_session


def test_dev_commands_denied_for_normal_user(config, layout, session_store) -> None:
    handler = CommandHandler(config)
    session = _session(layout, session_store)
    result = handler.dispatch("/debug", _context(config, session, is_dev=False))
    assert result is not None
    assert "administrators only" in result.reply


def test_dev_commands_allowed_for_dev(config, layout, session_store) -> None:
    handler = CommandHandler(config)
    session = _session(layout, session_store)
    result = handler.dispatch("/debug", _context(config, session, is_dev=True))
    assert result is not None
    assert "test-key" not in result.reply  # secrets redacted
    assert "***" in result.reply


def test_raw_requires_flag(config, layout, session_store) -> None:
    handler = CommandHandler(config)
    session = _session(layout, session_store)
    result = handler.dispatch("/raw", _context(config, session, is_dev=True))
    assert result is not None
    assert "allow_raw_xml" in result.reply


def test_non_command_returns_none(config, layout, session_store) -> None:
    handler = CommandHandler(config)
    session = _session(layout, session_store)
    assert handler.dispatch("just a message", _context(config, session)) is None
