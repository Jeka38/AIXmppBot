"""Persona and skills slash commands (SPEC §14, §30)."""

from __future__ import annotations

from pathlib import Path

from agent.config.models import PersonaConfig
from agent.llm.tokenizer import TokenEstimator
from agent.memory.store import DataLayout
from agent.personas.manager import PersonaManager
from agent.runtime.commands import CommandContext, CommandHandler
from agent.runtime.sessions import Session
from tests.conftest import make_config


class FakePersonaCommands:
    def __init__(self, manager: PersonaManager) -> None:
        self.manager = manager
        self.created: list[tuple[str, str]] = []

    def list(self, jid: str) -> list[str]:
        return self.manager.list_personas(jid)

    def active(self, jid: str) -> str:
        return self.manager.active_persona(jid)

    def select(self, jid: str, name: str) -> None:
        self.manager.set_active_persona(jid, name)

    def reset(self, jid: str) -> None:
        self.manager.reset_active_persona(jid)

    def create(self, jid: str, name: str, description: str = "") -> None:
        self.created.append((jid, name))
        self.manager.create_user_persona(jid, name, description=description)

    def delete(self, jid: str, name: str) -> None:
        self.manager.delete_user_persona(jid, name)

    def export(self, jid: str, name: str) -> str:
        return self.manager.export_user_persona(jid, name)

    def import_(self, jid: str, name: str, payload: str) -> None:
        self.manager.import_user_persona(jid, name, payload)


def _setup(tmp_path: Path):
    personas_dir = tmp_path / "personas"
    for name in ("general", "coder"):
        directory = personas_dir / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "persona.toml").write_text(
            f'[persona]\nid = "{name}"\nname = "{name}"\n', encoding="utf-8"
        )
    layout = DataLayout(tmp_path / "data")
    manager = PersonaManager(
        layout, PersonaConfig(default_persona="general"), global_dirs=[personas_dir]
    )
    handler = CommandHandler(make_config())
    session = Session.load(
        layout.scope_for_user("alice@example.com"),
        __import__(
            "agent.memory.store", fromlist=["FilesystemSessionStore"]
        ).FilesystemSessionStore(layout.memory_dir),
        TokenEstimator("t"),
        budget=1000,
    )
    context = CommandContext(
        session=session,
        config=make_config(),
        jid="alice@example.com",
        personas=FakePersonaCommands(manager),
        active_persona=manager.active_persona("alice@example.com"),
    )
    return handler, context, manager


def test_persona_show_active(tmp_path: Path) -> None:
    handler, context, _ = _setup(tmp_path)
    result = handler.dispatch("/persona", context)
    assert result is not None and "general" in result.reply


def test_persona_list(tmp_path: Path) -> None:
    handler, context, _ = _setup(tmp_path)
    result = handler.dispatch("/persona list", context)
    assert result is not None
    assert "general" in result.reply and "coder" in result.reply


def test_persona_select(tmp_path: Path) -> None:
    handler, context, manager = _setup(tmp_path)
    result = handler.dispatch("/persona coder", context)
    assert result is not None and "coder" in result.reply
    assert manager.active_persona("alice@example.com") == "coder"


def test_persona_unknown_name(tmp_path: Path) -> None:
    handler, context, _ = _setup(tmp_path)
    result = handler.dispatch("/persona does-not-exist", context)
    assert result is not None and "error" in result.reply.lower()


def test_persona_reset(tmp_path: Path) -> None:
    handler, context, manager = _setup(tmp_path)
    handler.dispatch("/persona coder", context)
    result = handler.dispatch("/persona reset", context)
    assert result is not None
    assert manager.active_persona("alice@example.com") == "general"


def test_persona_create_and_delete(tmp_path: Path) -> None:
    handler, context, manager = _setup(tmp_path)
    created = handler.dispatch("/persona create study-buddy", context)
    assert created is not None
    assert "study-buddy" in manager.list_user_personas("alice@example.com")

    deleted = handler.dispatch("/persona delete study-buddy", context)
    assert deleted is not None
    assert "study-buddy" not in manager.list_user_personas("alice@example.com")


def test_persona_create_requires_name(tmp_path: Path) -> None:
    handler, context, _ = _setup(tmp_path)
    result = handler.dispatch("/persona create", context)
    assert result is not None and "Usage" in result.reply


def test_skills_command_lists(tmp_path: Path) -> None:
    handler, context, _ = _setup(tmp_path)
    context.available_skills = ["web_search", "url_fetch"]
    result = handler.dispatch("/skills", context)
    assert result is not None
    assert "web_search" in result.reply and "url_fetch" in result.reply
