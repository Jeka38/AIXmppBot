"""Persona definition and loading (SPEC §12).

A persona is a directory containing ``persona.toml`` plus optional Markdown
files: ``identity.md``, ``personality.md``, ``instructions.md`` and ``context.md``.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from agent.personas.naming import InvalidPersonaName, validate_persona_name

_FILES = ("identity.md", "personality.md", "instructions.md", "context.md")


class PersonaDefaults(BaseModel):
    tier: str = "standard"
    skills: list[str] = Field(default_factory=list)
    max_tokens_per_message: int = 8192


class PersonaDefinition(BaseModel):
    id: str
    name: str
    description: str = ""
    defaults: PersonaDefaults = Field(default_factory=PersonaDefaults)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        try:
            return validate_persona_name(value)
        except InvalidPersonaName as exc:
            raise ValueError(f"invalid persona id ({exc.key})") from exc


@dataclass
class Persona:
    """A loaded persona: definition plus its Markdown documents."""

    definition: PersonaDefinition
    identity: str = ""
    personality: str = ""
    instructions: str = ""
    context: str = ""
    source_dir: str = ""

    #: Which resolution layer each document came from (for debugging).
    layers: dict[str, str] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.definition.id

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def tier(self) -> str:
        return self.definition.defaults.tier

    @property
    def skills(self) -> list[str]:
        return list(self.definition.defaults.skills)

    def render(self) -> str:
        """Compose the persona documents into a system-prompt fragment."""
        blocks: list[str] = []
        if self.identity:
            blocks.append(f"Identity:\n{self.identity}")
        if self.personality:
            blocks.append(f"Personality and tone:\n{self.personality}")
        if self.instructions:
            blocks.append(f"Instructions:\n{self.instructions}")
        if self.context:
            blocks.append(f"Background context:\n{self.context}")
        return "\n\n".join(blocks)


def load_persona_dir(directory: Path) -> Persona | None:
    """Load a persona from *directory*; ``None`` if there is no ``persona.toml``."""
    manifest = directory / "persona.toml"
    if not manifest.is_file():
        return None
    try:
        raw = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return None
    section = raw.get("persona", raw)
    definition = PersonaDefinition.model_validate(section)
    persona = Persona(definition=definition, source_dir=str(directory))
    for filename in _FILES:
        key = filename[:-3]
        path = directory / filename
        if path.is_file():
            setattr(persona, key, path.read_text(encoding="utf-8").strip())
    return persona


def list_persona_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        child for child in root.iterdir() if child.is_dir() and (child / "persona.toml").is_file()
    )
