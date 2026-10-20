"""Persona resolution, isolation and lifecycle (SPEC §12–14)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.config.models import PersonaConfig
from agent.errors import AuthorizationError, ConfigError
from agent.memory.store import DataLayout
from agent.personas.manager import PersonaManager


def _write_persona(
    directory: Path,
    name: str,
    *,
    identity: str = "",
    instructions: str = "",
    personality: str = "",
    context: str = "",
    tier: str = "standard",
    skills: list[str] | None = None,
) -> None:
    persona_dir = directory / name
    persona_dir.mkdir(parents=True, exist_ok=True)
    skills_toml = ", ".join(f'"{s}"' for s in (skills or []))
    (persona_dir / "persona.toml").write_text(
        f'[persona]\nid = "{name}"\nname = "{name.title()}"\n'
        f'[persona.defaults]\ntier = "{tier}"\nskills = [{skills_toml}]\n',
        encoding="utf-8",
    )
    for key, value in (
        ("identity", identity),
        ("personality", personality),
        ("instructions", instructions),
        ("context", context),
    ):
        if value:
            (persona_dir / f"{key}.md").write_text(value, encoding="utf-8")


def _manager(tmp_path: Path, global_dir: Path, **cfg) -> PersonaManager:
    layout = DataLayout(tmp_path / "data")
    config = PersonaConfig(default_persona="general", **cfg)
    return PersonaManager(layout, config, global_dirs=[global_dir])


@pytest.fixture
def globals_dir(tmp_path: Path) -> Path:
    root = tmp_path / "personas"
    _write_persona(root, "general", identity="I am the general assistant.")
    _write_persona(
        root, "coder", identity="I am a coding mentor.", tier="heavy", skills=["web_search"]
    )
    (root / "global_identity.md").write_text("", encoding="utf-8")
    return root


def test_resolve_global_persona(tmp_path: Path, globals_dir: Path) -> None:
    manager = _manager(tmp_path, globals_dir)
    persona = manager.resolve("alice@example.com")
    assert persona.id == "general"
    assert "general assistant" in persona.render()


def test_resolve_custom_active(tmp_path: Path, globals_dir: Path) -> None:
    manager = _manager(tmp_path, globals_dir)
    manager.set_active_persona("alice@example.com", "coder")
    persona = manager.resolve("alice@example.com")
    assert persona.id == "coder"
    assert persona.tier == "heavy"
    assert persona.skills == ["web_search"]


def test_resolution_prefers_user_identity(tmp_path: Path, globals_dir: Path) -> None:
    manager = _manager(tmp_path, globals_dir)
    user_dir = manager.layout.user_dir("alice@example.com")
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "identity.md").write_text("I am Alice's personal assistant.", encoding="utf-8")

    persona = manager.resolve("alice@example.com")
    assert persona.identity == "I am Alice's personal assistant."
    assert persona.layers["identity"] == "scope"
    # Non-overridden documents still come from the persona layer.
    assert persona.layers.get("identity") == "scope"


def test_active_persona_is_per_user(tmp_path: Path, globals_dir: Path) -> None:
    manager = _manager(tmp_path, globals_dir)
    manager.set_active_persona("alice@example.com", "coder")
    # Bob is unaffected.
    assert manager.active_persona("bob@example.com") == "general"


def test_unknown_persona_rejected(tmp_path: Path, globals_dir: Path) -> None:
    manager = _manager(tmp_path, globals_dir)
    with pytest.raises(ConfigError):
        manager.set_active_persona("alice@example.com", "does-not-exist")


def test_user_persona_lifecycle(tmp_path: Path, globals_dir: Path) -> None:
    manager = _manager(tmp_path, globals_dir)
    manager.create_user_persona("alice@example.com", "study-buddy")
    assert "study-buddy" in manager.list_user_personas("alice@example.com")

    manager.set_active_persona("alice@example.com", "study-buddy")
    assert manager.active_persona("alice@example.com") == "study-buddy"

    manager.delete_user_persona("alice@example.com", "study-buddy")
    assert "study-buddy" not in manager.list_user_personas("alice@example.com")
    assert manager.active_persona("alice@example.com") == "general"


def test_user_persona_limit(tmp_path: Path, globals_dir: Path) -> None:
    manager = _manager(tmp_path, globals_dir, max_user_personas=1)
    manager.create_user_persona("alice@example.com", "one")
    with pytest.raises(AuthorizationError):
        manager.create_user_persona("alice@example.com", "two")


def test_user_personas_can_be_disabled(tmp_path: Path, globals_dir: Path) -> None:
    manager = _manager(tmp_path, globals_dir, allow_user_personas=False)
    with pytest.raises(AuthorizationError):
        manager.create_user_persona("alice@example.com", "nope")


def test_user_personas_are_isolated(tmp_path: Path, globals_dir: Path) -> None:
    manager = _manager(tmp_path, globals_dir)
    manager.create_user_persona("alice@example.com", "alice-only")
    assert "alice-only" in manager.list_user_personas("alice@example.com")
    assert "alice-only" not in manager.list_user_personas("bob@example.com")


def test_invalid_persona_name_rejected(tmp_path: Path, globals_dir: Path) -> None:
    manager = _manager(tmp_path, globals_dir)
    with pytest.raises(ConfigError):
        manager.create_user_persona("alice@example.com", "../escape")


# -- room personas -----------------------------------------------------------


def test_room_persona_is_separate_from_user(tmp_path: Path, globals_dir: Path) -> None:
    manager = _manager(tmp_path, globals_dir)
    manager.set_active_persona("alice@example.com", "coder")
    manager.set_room_active_persona("room@conference.example.com", "general")

    assert manager.active_persona("alice@example.com") == "coder"
    assert manager.active_room_persona("room@conference.example.com") == "general"


def test_room_persona_lifecycle(tmp_path: Path, globals_dir: Path) -> None:
    manager = _manager(tmp_path, globals_dir)
    manager.create_room_persona("room@conference.example.com", "study-buddy")
    assert "study-buddy" in manager.list_room_personas("room@conference.example.com")

    manager.set_room_active_persona("room@conference.example.com", "study-buddy")
    assert manager.active_room_persona("room@conference.example.com") == "study-buddy"

    manager.delete_room_persona("room@conference.example.com", "study-buddy")
    assert "study-buddy" not in manager.list_room_personas("room@conference.example.com")
    assert manager.active_room_persona("room@conference.example.com") == "general"


def test_room_personas_are_isolated(tmp_path: Path, globals_dir: Path) -> None:
    manager = _manager(tmp_path, globals_dir)
    manager.create_room_persona("room1@conference.example.com", "only-room1")
    assert "only-room1" in manager.list_room_personas("room1@conference.example.com")
    assert "only-room1" not in manager.list_room_personas("room2@conference.example.com")


def test_room_persona_resolution(tmp_path: Path, globals_dir: Path) -> None:
    manager = _manager(tmp_path, globals_dir)
    room_dir = manager.layout.room_dir("room@conference.example.com")
    room_dir.mkdir(parents=True, exist_ok=True)
    (room_dir / "identity.md").write_text("I am the room assistant.", encoding="utf-8")

    persona = manager.resolve_room("room@conference.example.com")
    assert persona.identity == "I am the room assistant."
    assert persona.layers["identity"] == "scope"
