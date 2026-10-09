"""``persona_create`` skill: create a persona from a natural-language role.

The LLM drafts the persona documents (identity/personality/instructions/context)
from the user's description in the tool arguments; the runtime then persists
them through :class:`PersonaManager` in the caller's own scope. This keeps the
SPEC §65 rule: the model proposes, the runtime validates and writes.

The skill is deliberately self-contained: it never receives a filesystem path,
only the persona manager and the validated document text.
"""

from __future__ import annotations

import re
from typing import Any, Protocol

from agent.errors import AgentError
from agent.skills.base import BaseSkill, SkillContext, SkillResult

_MAX_DOC_CHARS = 4000
_NAME_RE = re.compile(r"[A-Za-z0-9._\-\u0400-\u04FF]+")

#: Phrases that indicate a persona attempting to remove the assistant's safety
#: rules or override the system policy. Such roles are refused rather than
#: stored (SPEC §32: external text must not become policy).
_UNSAFE_PATTERNS = [
    re.compile(p, re.I)
    for p in (
        r"ignore (all )?(previous|prior|above) instructions",
        r"disregard (the )?(safety|security|policy|rules)",
        r"without (any )?(restrictions|rules|limits|filter)",
        r"no (safety|security|content) (rules|policy|filter)",
        r"you (are|have) no (rules|limits|restrictions)",
        r"bypass (the )?(safety|security|policy)",
        r"act as (if you were )?(an? )?(unrestricted|uncensored)",
    )
]


def _looks_unsafe(documents: dict[str, str]) -> bool:
    text = "\n".join(documents.values())
    return any(pattern.search(text) for pattern in _UNSAFE_PATTERNS)


class _PersonaWriter(Protocol):
    """The slice of PersonaManager the persona skill needs."""

    def create_user_persona(
        self,
        jid: str,
        name: str,
        *,
        description: str = "",
        documents: dict[str, str] | None = None,
        tier: str = "standard",
        skills: list[str] | None = None,
    ) -> None: ...

    def create_room_persona(
        self,
        room_jid: str,
        name: str,
        *,
        description: str = "",
        documents: dict[str, str] | None = None,
        tier: str = "standard",
        skills: list[str] | None = None,
    ) -> None: ...

    def set_active_persona(self, jid: str, name: str) -> None: ...

    def set_room_active_persona(self, room_jid: str, name: str) -> None: ...


class PersonaCreateSkill(BaseSkill):
    skill_name = "persona_create"
    skill_description = (
        "Create a reusable persona (assistant role/character) that the user can "
        "activate later with /persona. Use when the user asks you to create a "
        "persona/role, e.g. 'create a strict code reviewer'. You must write the "
        "role documents yourself based on their request. The persona is stored "
        "in the current user's or room's own space."
    )
    schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Short persona name (letters/digits/._-, any language), e.g. "
                    "'code-reviewer' or 'ревьюер'."
                ),
            },
            "description": {
                "type": "string",
                "description": "One-line description of the persona.",
            },
            "identity": {
                "type": "string",
                "description": "Who the assistant is (first person, 1-3 sentences).",
            },
            "personality": {
                "type": "string",
                "description": "Tone and character traits.",
            },
            "instructions": {
                "type": "string",
                "description": "Behavioural rules the assistant must follow.",
            },
            "context": {
                "type": "string",
                "description": "Optional background domain context.",
            },
            "tier": {
                "type": "string",
                "enum": ["light", "standard", "heavy", "vision"],
                "description": "Model tier for this persona. Default 'standard'.",
            },
            "skills": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional subset of skills this persona may use.",
            },
        },
        "required": ["name", "identity", "instructions"],
        "additionalProperties": False,
    }
    required_capabilities = ["filesystem"]

    async def execute(self, arguments: dict[str, Any], context: SkillContext) -> SkillResult:
        from agent.i18n import get_translator

        tr = get_translator()
        if context.personas is None:
            return SkillResult.failure(tr.t("persona.unavailable"))

        name = str(arguments["name"]).strip()
        documents = {
            "identity": _clip(arguments.get("identity", "")),
            "personality": _clip(arguments.get("personality", "")),
            "instructions": _clip(arguments.get("instructions", "")),
            "context": _clip(arguments.get("context", "")),
        }
        description = _clip(arguments.get("description", ""), limit=300)
        tier = str(arguments.get("tier", "standard"))
        raw_skills = arguments.get("skills") or []
        known = set(context.data.get("available_skills") or [])
        # Only keep real skill names; the model sometimes writes descriptions.
        skills = [
            str(s).strip()
            for s in raw_skills
            if str(s).strip() and (not known or str(s).strip() in known)
        ]

        if _looks_unsafe({**documents, "description": description}):
            return SkillResult.failure(tr.t("persona.unsafe"))

        try:
            _create(context, name, description, documents, tier, skills)
        except AgentError as exc:
            return SkillResult.failure(f"{tr.t('persona.error', error=exc)}")
        except Exception as exc:  # noqa: BLE001 - never crash the loop
            return SkillResult.failure(f"{tr.t('persona.error', error=exc)}")

        # Auto-activate the freshly created persona for immediate effect.
        try:
            _activate(context, name)
        except Exception:  # noqa: BLE001 - activation is best-effort
            pass
        return SkillResult.success(
            tr.t("persona.created", name=name),
            persona=name,
            activated=True,
        )


def _create(
    context: SkillContext,
    name: str,
    description: str,
    documents: dict[str, str],
    tier: str,
    skills: list[str],
) -> None:
    manager: _PersonaWriter = context.personas  # type: ignore[assignment]
    if context.room_jid:
        manager.create_room_persona(
            context.room_jid,
            name,
            description=description,
            documents=documents,
            tier=tier,
            skills=skills,
        )
    else:
        manager.create_user_persona(
            context.jid,
            name,
            description=description,
            documents=documents,
            tier=tier,
            skills=skills,
        )


def _activate(context: SkillContext, name: str) -> None:
    manager: _PersonaWriter = context.personas  # type: ignore[assignment]
    if context.room_jid:
        manager.set_room_active_persona(context.room_jid, name)
    else:
        manager.set_active_persona(context.jid, name)


def _clip(value: object, *, limit: int = _MAX_DOC_CHARS) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        text = text[:limit] + "\n… [truncated]"
    return text
