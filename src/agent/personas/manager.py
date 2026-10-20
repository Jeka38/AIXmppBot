"""Persona manager: resolution, selection and user-defined personas (SPEC §12–14).

Resolution order for each persona document (first non-empty wins):

1. ``<scope>/identity.md``            (the user's or room's own file)
2. ``<scope>/personas/<active>/identity.md``
3. ``<global>/<active>/identity.md``
4. ``<global>/identity.md``

A persona *scope* is either a user (DM) or a room (MUC). User personas live in
``<user>/personas/<name>/``; room personas in ``<room>/personas/<name>/``. Global
personas live in ``config/personas/`` and ``data/personas/``. The same limits
apply to both scopes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from agent.config.models import PersonaConfig
from agent.errors import AuthorizationError, ConfigError
from agent.memory.store import DataLayout, jid_to_dirname
from agent.personas.loader import (
    Persona,
    PersonaDefinition,
    list_persona_dirs,
    load_persona_dir,
)
from agent.personas.naming import (
    InvalidPersonaName,
    normalize_persona_name,
    validate_persona_name,
)

log = logging.getLogger(__name__)

_DOCS = ("identity", "personality", "instructions", "context")

_PERSONA_TOML_TEMPLATE = """[persona]
id = "{name}"
name = "{name}"
description = ""

[persona.defaults]
tier = "standard"
skills = []
max_tokens_per_message = 8192
"""


@dataclass(frozen=True)
class _Scope:
    """Storage locations for one persona scope (a user or a room)."""

    root: Path
    label: str

    @property
    def personas_root(self) -> Path:
        return self.root / "personas"


class PersonaManager:
    def __init__(
        self,
        layout: DataLayout,
        config: PersonaConfig,
        *,
        global_dirs: list[Path] | None = None,
    ) -> None:
        self.layout = layout
        self.config = config
        self.global_dirs = global_dirs or [layout.personas_dir]

    # -- scopes ----------------------------------------------------------

    def _user_scope(self, jid: str) -> _Scope:
        return _Scope(root=self.layout.user_dir(jid), label=f"user {jid}")

    def _room_scope(self, room_jid: str) -> _Scope:
        return _Scope(root=self.layout.room_dir(room_jid), label=f"room {room_jid}")

    # -- discovery (shared) ----------------------------------------------

    def list_global_personas(self) -> list[str]:
        names: list[str] = []
        for directory in self.global_dirs:
            for path in list_persona_dirs(directory):
                if path.name not in names:
                    names.append(path.name)
        return names

    def _list_local_personas(self, scope: _Scope) -> list[str]:
        return [path.name for path in list_persona_dirs(scope.personas_root)]

    def _list_personas(self, scope: _Scope) -> list[str]:
        names = self.list_global_personas()
        for name in self._list_local_personas(scope):
            if name not in names:
                names.append(name)
        return names

    def _exists(self, scope: _Scope, name: str) -> bool:
        return name in self._list_local_personas(scope) or name in self.list_global_personas()

    # -- active persona (shared) -----------------------------------------

    @staticmethod
    def _active_file(scope: _Scope) -> Path:
        return scope.root / "active_persona.txt"

    def _active_persona(self, scope: _Scope) -> str:
        path = self._active_file(scope)
        if path.is_file():
            value = normalize_persona_name(path.read_text(encoding="utf-8"))
            if value:
                return value
        return self.config.default_persona

    def _set_active_persona(self, scope: _Scope, name: str) -> None:
        name = normalize_persona_name(name)
        if not self._exists(scope, name):
            raise ConfigError(f"Unknown persona: {name!r}")
        path = self._active_file(scope)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name + "\n", encoding="utf-8")

    def _reset_active_persona(self, scope: _Scope) -> None:
        path = self._active_file(scope)
        if path.is_file():
            path.unlink()

    # -- resolution (shared) ---------------------------------------------

    def _resolve(self, scope: _Scope, active: str | None = None) -> Persona:
        name = normalize_persona_name(active or self._active_persona(scope))
        user_persona_dir = scope.personas_root / name

        definition = self._load_definition(name, user_persona_dir)
        persona = Persona(definition=definition, source_dir=str(user_persona_dir))

        for doc in _DOCS:
            value, layer = self._resolve_doc(doc, scope, user_persona_dir, name)
            setattr(persona, doc, value)
            if value:
                persona.layers[doc] = layer
        return persona

    def _load_definition(self, name: str, user_persona_dir: Path) -> PersonaDefinition:
        for candidate in (user_persona_dir, *_global_persona_dirs(self.global_dirs, name)):
            loaded = load_persona_dir(candidate)
            if loaded is not None:
                return loaded.definition
        return PersonaDefinition(id=name, name=name, description="")

    def _resolve_doc(
        self, doc: str, scope: _Scope, user_persona_dir: Path, name: str
    ) -> tuple[str, str]:
        candidates: list[tuple[Path, str]] = [
            (scope.root / f"{doc}.md", "scope"),
            (user_persona_dir / f"{doc}.md", "scope-persona"),
        ]
        for global_dir in self.global_dirs:
            candidates.append((global_dir / name / f"{doc}.md", "persona"))
        for global_dir in self.global_dirs:
            candidates.append((global_dir / f"{doc}.md", "global"))
        for path, layer in candidates:
            if path.is_file():
                text = path.read_text(encoding="utf-8").strip()
                if text:
                    return text, layer
        return "", ""

    # -- user persona management (shared) --------------------------------

    def _create_persona(
        self,
        scope: _Scope,
        name: str,
        *,
        description: str = "",
        documents: dict[str, str] | None = None,
        tier: str = "standard",
        skills: list[str] | None = None,
    ) -> None:
        if not self.config.allow_user_personas:
            raise AuthorizationError("User-defined personas are disabled")
        try:
            name = validate_persona_name(name)
        except InvalidPersonaName as exc:
            from agent.i18n import get_translator

            raise ConfigError(get_translator().t(exc.key, **exc.params)) from exc
        if self._exists(scope, name):
            raise ConfigError(f"Persona already exists: {name!r}")
        existing = self._list_local_personas(scope)
        if len(existing) >= self.config.max_user_personas:
            raise AuthorizationError(f"Persona limit reached ({self.config.max_user_personas})")
        directory = scope.personas_root / name
        directory.mkdir(parents=True, exist_ok=True)
        skill_list = ", ".join(f'"{skill}"' for skill in (skills or []))
        manifest = (
            _PERSONA_TOML_TEMPLATE.format(name=name)
            .replace('tier = "standard"', f'tier = "{tier}"')
            .replace("skills = []", f"skills = [{skill_list}]")
        )
        if description:
            manifest = manifest.replace('description = ""', f'description = "{description}"')
        (directory / "persona.toml").write_text(manifest, encoding="utf-8")
        docs = documents or {}
        # Always create the standard documents so the persona is editable later.
        for doc in _DOCS:
            content = docs.get(doc, "")
            (directory / f"{doc}.md").write_text(content, encoding="utf-8")

    def _delete_persona(self, scope: _Scope, name: str) -> None:
        name = normalize_persona_name(name)
        directory = (scope.personas_root / name).resolve()
        root = scope.personas_root.resolve()
        if not directory.is_relative_to(root) or not directory.is_dir():
            raise ConfigError(f"No such persona: {name!r}")
        if not self.config.allow_user_personas:
            raise AuthorizationError("User-defined personas are disabled")
        for child in directory.iterdir():
            if child.is_file():
                child.unlink()
        directory.rmdir()
        if self._active_persona(scope) == name:
            self._reset_active_persona(scope)

    # -- user-facing API (DM) --------------------------------------------

    def list_user_personas(self, jid: str) -> list[str]:
        return self._list_local_personas(self._user_scope(jid))

    def list_personas(self, jid: str) -> list[str]:
        return self._list_personas(self._user_scope(jid))

    def exists(self, name: str, jid: str | None = None) -> bool:
        if jid is not None and name in self._list_local_personas(self._user_scope(jid)):
            return True
        return name in self.list_global_personas()

    def active_persona(self, jid: str) -> str:
        return self._active_persona(self._user_scope(jid))

    def set_active_persona(self, jid: str, name: str) -> None:
        self._set_active_persona(self._user_scope(jid), name)

    def reset_active_persona(self, jid: str) -> None:
        self._reset_active_persona(self._user_scope(jid))

    def resolve(self, jid: str, active: str | None = None) -> Persona:
        return self._resolve(self._user_scope(jid), active)

    def create_user_persona(
        self,
        jid: str,
        name: str,
        *,
        description: str = "",
        documents: dict[str, str] | None = None,
        tier: str = "standard",
        skills: list[str] | None = None,
    ) -> None:
        self._create_persona(
            self._user_scope(jid),
            name,
            description=description,
            documents=documents,
            tier=tier,
            skills=skills,
        )

    def delete_user_persona(self, jid: str, name: str) -> None:
        self._delete_persona(self._user_scope(jid), name)

    # -- room-facing API (MUC) -------------------------------------------

    def list_room_personas(self, room_jid: str) -> list[str]:
        return self._list_personas(self._room_scope(room_jid))

    def active_room_persona(self, room_jid: str) -> str:
        return self._active_persona(self._room_scope(room_jid))

    def set_room_active_persona(self, room_jid: str, name: str) -> None:
        self._set_active_persona(self._room_scope(room_jid), name)

    def reset_room_active_persona(self, room_jid: str) -> None:
        self._reset_active_persona(self._room_scope(room_jid))

    def resolve_room(self, room_jid: str, active: str | None = None) -> Persona:
        return self._resolve(self._room_scope(room_jid), active)

    def create_room_persona(
        self,
        room_jid: str,
        name: str,
        *,
        description: str = "",
        documents: dict[str, str] | None = None,
        tier: str = "standard",
        skills: list[str] | None = None,
    ) -> None:
        self._create_persona(
            self._room_scope(room_jid),
            name,
            description=description,
            documents=documents,
            tier=tier,
            skills=skills,
        )

    def delete_room_persona(self, room_jid: str, name: str) -> None:
        self._delete_persona(self._room_scope(room_jid), name)


def _global_persona_dirs(global_dirs: list[Path], name: str) -> list[Path]:
    return [directory / name for directory in global_dirs]


def user_scope(jid: str) -> str:
    return f"users/{jid_to_dirname(jid)}"
