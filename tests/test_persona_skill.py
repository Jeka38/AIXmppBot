"""Creating personas from a natural-language role via the persona_create skill."""

from __future__ import annotations

from pathlib import Path

from agent.config.models import PersonaConfig
from agent.memory.store import DataLayout
from agent.personas.manager import PersonaManager
from agent.security.capabilities import CapabilitySet
from agent.skills.base import SkillContext
from agent.skills.persona import PersonaCreateSkill


def _setup(tmp_path: Path, *, room: bool = False):
    layout = DataLayout(tmp_path / "data")
    manager = PersonaManager(layout, PersonaConfig(default_persona="general-assistant"))
    context = SkillContext(
        jid="alice@example.com",
        room_jid="room@conference.example.com" if room else None,
        capabilities=CapabilitySet(),
        personas=manager,
    )
    return PersonaCreateSkill(), manager, context


async def test_creates_persona_from_role(tmp_path: Path) -> None:
    skill, manager, context = _setup(tmp_path)
    result = await skill.execute(
        {
            "name": "code-reviewer",
            "description": "Strict code reviewer",
            "identity": "I am a strict senior code reviewer.",
            "personality": "Blunt but fair.",
            "instructions": "Always point out security issues first.",
        },
        context,
    )
    assert result.ok
    assert "code-reviewer" in manager.list_user_personas("alice@example.com")

    # The generated documents are persisted and used.
    persona = manager.resolve("alice@example.com", "code-reviewer")
    assert "strict senior code reviewer" in persona.identity
    assert "security issues" in persona.instructions
    assert persona.personality == "Blunt but fair."


async def test_auto_activates_new_persona(tmp_path: Path) -> None:
    skill, manager, context = _setup(tmp_path)
    await skill.execute(
        {"name": "mentor", "identity": "I mentor.", "instructions": "Be patient."},
        context,
    )
    assert manager.active_persona("alice@example.com") == "mentor"


async def test_cyrillic_name(tmp_path: Path) -> None:
    skill, manager, context = _setup(tmp_path)
    result = await skill.execute(
        {"name": "Помощник", "identity": "Я помощник.", "instructions": "Помогай."},
        context,
    )
    assert result.ok
    assert "помощник" in manager.list_user_personas("alice@example.com")


async def test_room_scoped_persona(tmp_path: Path) -> None:
    skill, manager, context = _setup(tmp_path, room=True)
    result = await skill.execute(
        {"name": "room-bot", "identity": "I am the room bot.", "instructions": "Be terse."},
        context,
    )
    assert result.ok
    assert "room-bot" in manager.list_room_personas("room@conference.example.com")
    assert manager.active_room_persona("room@conference.example.com") == "room-bot"
    # The user's own scope is untouched.
    assert "room-bot" not in manager.list_user_personas("alice@example.com")


async def test_tier_and_skills_persisted(tmp_path: Path) -> None:
    skill, manager, context = _setup(tmp_path)
    await skill.execute(
        {
            "name": "researcher",
            "identity": "I research.",
            "instructions": "Use the web.",
            "tier": "heavy",
            "skills": ["web_search", "url_fetch"],
        },
        context,
    )
    persona = manager.resolve("alice@example.com", "researcher")
    assert persona.tier == "heavy"
    assert set(persona.skills) == {"web_search", "url_fetch"}


async def test_invalid_name_reported(tmp_path: Path) -> None:
    skill, _, context = _setup(tmp_path)
    result = await skill.execute(
        {"name": "../evil", "identity": "x", "instructions": "y"},
        context,
    )
    assert not result.ok
    assert "persona" in (result.error or "").lower() or "имя" in (result.error or "").lower()


async def test_missing_required_arguments_rejected(tmp_path: Path) -> None:
    from agent.skills.registry import SkillRegistry

    skill, _, context = _setup(tmp_path)
    registry = SkillRegistry()
    registry.register(skill, grants=[])
    # 'identity' and 'instructions' are required by the schema.
    result = await registry.execute("persona_create", {"name": "x"}, context)
    assert not result.ok
    assert "Invalid arguments" in (result.error or "")


async def test_no_persona_manager_fails_gracefully(tmp_path: Path) -> None:
    skill = PersonaCreateSkill()
    context = SkillContext(jid="alice@example.com")
    result = await skill.execute({"name": "x", "identity": "i", "instructions": "n"}, context)
    assert not result.ok


async def test_unknown_skill_names_filtered(tmp_path: Path) -> None:
    skill, manager, context = _setup(tmp_path)
    context.data["available_skills"] = ["web_search", "url_fetch"]
    await skill.execute(
        {
            "name": "reviewer",
            "identity": "I review.",
            "instructions": "Review.",
            "skills": ["Static code analysis", "web_search", "Performance profiling"],
        },
        context,
    )
    persona = manager.resolve("alice@example.com", "reviewer")
    # Only real skill names survive; descriptions do not disable every tool.
    assert persona.skills == ["web_search"]


async def test_duplicate_persona_rejected(tmp_path: Path) -> None:
    skill, _, context = _setup(tmp_path)
    args = {"name": "dup", "identity": "i", "instructions": "n"}
    assert (await skill.execute(args, context)).ok
    second = await skill.execute({**args, "identity": "other"}, context)
    assert not second.ok
