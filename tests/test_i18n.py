"""Internationalisation: catalogs, fallback and Russian command output."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.i18n import (
    Translator,
    normalize_locale,
    supported_locales,
    t,
)
from agent.llm.tokenizer import TokenEstimator
from agent.memory.store import DataLayout, FilesystemSessionStore
from agent.runtime.commands import CommandContext, CommandHandler
from agent.runtime.sessions import Session
from tests.conftest import make_config


def test_supported_locales() -> None:
    locales = supported_locales()
    assert "en" in locales and "ru" in locales


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ru", "ru"),
        ("ru-RU", "ru"),
        ("ru_RU", "ru"),
        ("RU", "ru"),
        ("en-US", "en"),
        ("de", "en"),  # unknown -> default fallback
        ("", "en"),
    ],
)
def test_normalize_locale(raw: str, expected: str) -> None:
    assert normalize_locale(raw) == expected


def test_catalogs_have_matching_keys() -> None:
    from agent.i18n import _catalog

    en = set(_catalog("en"))
    ru = set(_catalog("ru"))
    missing_in_ru = en - ru
    assert not missing_in_ru, f"keys missing from ru.json: {sorted(missing_in_ru)}"


def test_translate_formats_placeholders() -> None:
    assert Translator("ru").t("status.messages", count=7) == "Сообщений в сессии: 7"
    assert Translator("en").t("status.messages", count=7) == "Messages in session: 7"


def test_translate_falls_back_to_english() -> None:
    assert Translator("xx").t("command.ping") == Translator("en").t("command.ping")


def test_translate_returns_key_when_missing() -> None:
    assert Translator("ru").t("does.not.exist") == "does.not.exist"


def test_global_translate_helper() -> None:
    assert t("command.ping") in {"pong", "понг"}


# -- command output in Russian -----------------------------------------------


def _context(config, session, *, translator, is_dev=False) -> CommandContext:
    return CommandContext(
        session=session,
        config=config,
        jid="alice@example.com",
        is_dev=is_dev,
        translator=translator,
    )


def _session(layout, store) -> Session:
    return Session.load(
        layout.scope_for_user("alice@example.com"),
        store,
        TokenEstimator("test"),
        budget=1000,
    )


def test_help_hides_admin_section_for_regular_user(tmp_path: Path) -> None:
    config = make_config()
    layout = DataLayout(tmp_path / "data")
    store = FilesystemSessionStore(layout.memory_dir)
    handler = CommandHandler(config, translator=Translator("ru"))
    session = _session(layout, store)

    result = handler.dispatch(
        "/help", _context(config, session, translator=Translator("ru"), is_dev=False)
    )
    assert result is not None
    assert "Доступные команды" in result.reply
    assert "Персоны" in result.reply
    # Developer section must not leak to ordinary users.
    assert "Команды разработчика" not in result.reply
    assert "/debug" not in result.reply
    assert "/tier" not in result.reply


def test_help_shows_admin_section_for_admin(tmp_path: Path) -> None:
    config = make_config()
    layout = DataLayout(tmp_path / "data")
    store = FilesystemSessionStore(layout.memory_dir)
    handler = CommandHandler(config, translator=Translator("ru"))
    session = _session(layout, store)

    result = handler.dispatch(
        "/help", _context(config, session, translator=Translator("ru"), is_dev=True)
    )
    assert result is not None
    assert "Доступные команды" in result.reply
    assert "Команды разработчика" in result.reply
    assert "/tier" in result.reply


def test_ping_in_russian(tmp_path: Path) -> None:
    config = make_config()
    layout = DataLayout(tmp_path / "data")
    store = FilesystemSessionStore(layout.memory_dir)
    handler = CommandHandler(config, translator=Translator("ru"))
    session = _session(layout, store)

    result = handler.dispatch("/ping", _context(config, session, translator=Translator("ru")))
    assert result is not None and result.reply == "понг"


def test_status_in_russian(tmp_path: Path) -> None:
    config = make_config()
    layout = DataLayout(tmp_path / "data")
    store = FilesystemSessionStore(layout.memory_dir)
    handler = CommandHandler(config, translator=Translator("ru"))
    session = _session(layout, store)

    result = handler.dispatch("/status", _context(config, session, translator=Translator("ru")))
    assert result is not None
    assert "Сообщений в сессии" in result.reply


def test_unknown_command_in_russian(tmp_path: Path) -> None:
    config = make_config()
    layout = DataLayout(tmp_path / "data")
    store = FilesystemSessionStore(layout.memory_dir)
    handler = CommandHandler(config, translator=Translator("ru"))
    session = _session(layout, store)

    result = handler.dispatch("/nope", _context(config, session, translator=Translator("ru")))
    assert result is not None
    assert "Неизвестная команда" in result.reply


def test_help_in_english_default(tmp_path: Path) -> None:
    config = make_config()
    layout = DataLayout(tmp_path / "data")
    store = FilesystemSessionStore(layout.memory_dir)
    handler = CommandHandler(config)
    session = _session(layout, store)

    result = handler.dispatch("/help", _context(config, session, translator=Translator("en")))
    assert result is not None and "Available commands" in result.reply


async def test_runtime_uses_config_language(tmp_path: Path) -> None:
    from agent.runtime.runtime import AgentRuntime
    from agent.skills.registry import SkillRegistry
    from tests.conftest import FakeLLMClient
    from tests.test_runtime import FakeRouter

    config = make_config()
    config.agent.language = "ru"
    layout = DataLayout(tmp_path / "data")
    store = FilesystemSessionStore(layout.memory_dir)
    runtime = AgentRuntime(
        config,
        FakeRouter(FakeLLMClient()),  # type: ignore[arg-type]
        SkillRegistry(),
        store,
        layout,
    )
    reply = await runtime.handle_dm("alice@example.com", "/ping")
    assert reply == "понг"
