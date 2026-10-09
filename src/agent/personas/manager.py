"""Persona manager: resolution, selection and user-defined personas (SPEC §12–14).

Resolution order for each persona document (first non-empty wins):

1. ``<user>/identity.md``
2. ``<user>/personas/<active>/identity.md``
3. ``<global>/<active>/identity.md``
4. ``<global>/identity.md``

Global personas live in ``config/personas/`` and ``data/personas/``. User
personas are confined to ``<user>/personas/<name>/`` and validated against the
configured limits.
"""

from __future__ import annotations

import json
import logging
import re
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

log = logging.getLogger(__name__)

_DOCS = ("identity", "personality", "instructions", "context")
_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

_PERSONA_TOML_TEMPLATE = """[persona]
id = "{name}"
name = "{name}"
description = ""

[persona.defaults]
tier = "standard"
skills = []
max_tokens_per_message = 8192
"""


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

    # -- discovery -------------------------------------------------------

    def list_global_personas(self) -> list[str]:
        names: list[str] = []
        for directory in self.global_dirs:
            for path in list_persona_dirs(directory):
                if path.name not in names:
                    names.append(path.name)
        return names

    def list_user_personas(self, jid: str) -> list[str]:
        root = self.layout.user_personas_dir(jid)
        return [path.name for path in list_persona_dirs(root)]

    def list_personas(self, jid: str) -> list[str]:
        names = self.list_global_personas()
        for name in self.list_user_personas(jid):
            if name not in names:
                names.append(name)
        return names

    def exists(self, name: str, jid: str | None = None) -> bool:
        if jid is not None and name in self.list_user_personas(jid):
            return True
        return name in self.list_global_personas()

    # -- active persona --------------------------------------------------

    def _active_file(self, jid: str) -> Path:
        return self.layout.user_dir(jid) / "active_persona.txt"

    def active_persona(self, jid: str) -> str:
        path = self._active_file(jid)
        if path.is_file():
            value = path.read_text(encoding="utf-8").strip().lower()
            if value:
                return value
        return self.config.default_persona

    def set_active_persona(self, jid: str, name: str) -> None:
        name = name.strip().lower()
        if not self.exists(name, jid):
            raise ConfigError(f"Unknown persona: {name!r}")
        path = self._active_file(jid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name + "\n", encoding="utf-8")

    def reset_active_persona(self, jid: str) -> None:
        path = self._active_file(jid)
        if path.is_file():
            path.unlink()

    # -- resolution ------------------------------------------------------

    def resolve(self, jid: str, active: str | None = None) -> Persona:
        """Compose the effective persona for *jid* honouring the SPEC §13 order."""
        name = (active or self.active_persona(jid)).strip().lower()
        user_dir = self.layout.user_dir(jid)
        user_persona_dir = user_dir / "personas" / name

        definition = self._load_definition(name, user_persona_dir)
        persona = Persona(definition=definition, source_dir=str(user_persona_dir))

        for doc in _DOCS:
            value, layer = self._resolve_doc(doc, user_dir, user_persona_dir, name)
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
        self, doc: str, user_dir: Path, user_persona_dir: Path, name: str
    ) -> tuple[str, str]:
        candidates: list[tuple[Path, str]] = [
            (user_dir / f"{doc}.md", "user"),
            (user_persona_dir / f"{doc}.md", "user-persona"),
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

    # -- user persona management -----------------------------------------

    def create_user_persona(self, jid: str, name: str, *, description: str = "") -> None:
        if not self.config.allow_user_personas:
            raise AuthorizationError("User-defined personas are disabled")
        name = name.strip().lower()
        if not _NAME_PATTERN.match(name):
            raise ConfigError(
                "Persona name must be lowercase alphanumeric with . _ - (max 64 chars)"
            )
        if self.exists(name, jid):
            raise ConfigError(f"Persona already exists: {name!r}")
        existing = self.list_user_personas(jid)
        if len(existing) >= self.config.max_user_personas:
            raise AuthorizationError(f"Persona limit reached ({self.config.max_user_personas})")
        directory = self.layout.user_personas_dir(jid) / name
        directory.mkdir(parents=True, exist_ok=True)
        manifest = _PERSONA_TOML_TEMPLATE.format(name=name)
        if description:
            manifest = manifest.replace('description = ""', f'description = "{description}"')
        (directory / "persona.toml").write_text(manifest, encoding="utf-8")
        (directory / "identity.md").write_text("", encoding="utf-8")
        (directory / "instructions.md").write_text("", encoding="utf-8")

    def delete_user_persona(self, jid: str, name: str) -> None:
        name = name.strip().lower()
        directory = (self.layout.user_personas_dir(jid) / name).resolve()
        user_root = self.layout.user_personas_dir(jid).resolve()
        if not directory.is_relative_to(user_root) or not directory.is_dir():
            raise ConfigError(f"No such user persona: {name!r}")
        if not self.config.allow_user_personas:
            raise AuthorizationError("User-defined personas are disabled")
        for child in directory.iterdir():
            if child.is_file():
                child.unlink()
        directory.rmdir()
        if self.active_persona(jid) == name:
            self.reset_active_persona(jid)

    def export_user_persona(self, jid: str, name: str) -> str:
        name = name.strip().lower()
        directory = (self.layout.user_personas_dir(jid) / name).resolve()
        user_root = self.layout.user_personas_dir(jid).resolve()
        if not directory.is_relative_to(user_root) or not directory.is_dir():
            raise ConfigError(f"No such user persona: {name!r}")
        payload: dict[str, str] = {}
        for path in sorted(directory.iterdir()):
            if path.is_file():
                payload[path.name] = path.read_text(encoding="utf-8")
        return json.dumps({"persona": name, "files": payload}, ensure_ascii=False)

    def import_user_persona(self, jid: str, name: str, payload_json: str) -> None:
        try:
            data = json.loads(payload_json)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Invalid persona JSON: {exc}") from exc
        files = data.get("files")
        if not isinstance(files, dict) or not files:
            raise ConfigError("Persona JSON must contain a non-empty 'files' object")
        self.create_user_persona(jid, name)
        directory = self.layout.user_personas_dir(jid) / name.strip().lower()
        allowed = {"persona.toml", "identity.md", "personality.md", "instructions.md", "context.md"}
        for filename, content in files.items():
            if filename not in allowed or not isinstance(content, str):
                continue
            (directory / filename).write_text(content, encoding="utf-8")


def _global_persona_dirs(global_dirs: list[Path], name: str) -> list[Path]:
    return [directory / name for directory in global_dirs]


def user_scope(jid: str) -> str:
    return f"users/{jid_to_dirname(jid)}"
