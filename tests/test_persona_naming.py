"""Persona name validation: Unicode (Cyrillic) support and path safety."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.config.models import PersonaConfig
from agent.errors import ConfigError
from agent.memory.store import DataLayout
from agent.personas.manager import PersonaManager
from agent.personas.naming import (
    InvalidPersonaName,
    normalize_persona_name,
    validate_persona_name,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("помощник", "помощник"),
        ("Учитель", "учитель"),
        ("кодер-бот", "кодер-бот"),
        ("Помощник_1", "помощник_1"),
        ("naïve", "naïve"),
        ("café", "café"),
        ("General-Assistant", "general-assistant"),
    ],
)
def test_valid_names(raw: str, expected: str) -> None:
    assert validate_persona_name(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "../evil",
        "a/b",
        "a\\b",
        ".hidden",
        "-bad",
        "_bad",
        "bad name",
        "x@y",
        "a" * 65,
        "..",
        ".",
    ],
)
def test_invalid_names(raw: str) -> None:
    with pytest.raises(InvalidPersonaName):
        validate_persona_name(raw)


def test_normalize_is_nfc_and_lower() -> None:
    # "Й" (decomposed) normalises to a single Cyrillic codepoint, lowercased.
    assert normalize_persona_name("Й") == "\u0439"


def test_error_exposes_i18n_key() -> None:
    with pytest.raises(InvalidPersonaName) as exc:
        validate_persona_name("a/b")
    assert exc.value.key == "persona.name.invalid"


def _manager(tmp_path: Path) -> PersonaManager:
    layout = DataLayout(tmp_path / "data")
    return PersonaManager(layout, PersonaConfig(default_persona="general-assistant"))


def test_create_cyrillic_persona(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.create_user_persona("alice@example.com", "Помощник")
    assert "помощник" in manager.list_user_personas("alice@example.com")

    manager.set_active_persona("alice@example.com", "Помощник")
    assert manager.active_persona("alice@example.com") == "помощник"


def test_create_cyrillic_room_persona(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.create_room_persona("room@conference.example.com", "Учитель")
    assert "учитель" in manager.list_room_personas("room@conference.example.com")
    manager.set_room_active_persona("room@conference.example.com", "Учитель")
    assert manager.active_room_persona("room@conference.example.com") == "учитель"


def test_cyrillic_persona_dir_created(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.create_user_persona("alice@example.com", "Помощник")
    directory = manager.layout.user_dir("alice@example.com") / "personas" / "помощник"
    assert (directory / "persona.toml").is_file()


def test_traversal_name_rejected_with_localised_error(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    with pytest.raises(ConfigError):
        manager.create_user_persona("alice@example.com", "../escape")
    # No directory escaped the user's persona root.
    personas_root = manager.layout.user_personas_dir("alice@example.com")
    assert not (personas_root / ".." / "escape").exists()
