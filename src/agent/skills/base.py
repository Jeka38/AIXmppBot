"""Skill protocol, invocation context and result types (SPEC §18, §31).

A skill declares a JSON Schema for its parameters and a capability
declaration. The runtime validates arguments and capabilities *before*
:meth:`Skill.execute` is called.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from agent.errors import AuthorizationError
from agent.llm.base import ToolSpec
from agent.security.capabilities import CapabilityGrant, CapabilitySet
from agent.security.credentials import CredentialStore, Redactor


class SkillResult(BaseModel):
    """Outcome of a skill execution."""

    ok: bool = True
    content: str = ""

    #: Structured payload for the model; kept separate from ``content`` so it
    #: can be redacted/summarised independently.
    data: dict[str, Any] = Field(default_factory=dict)

    error: str | None = None

    #: Set when the skill must not run until the user explicitly confirms.
    confirmation_required: bool = False
    confirmation_prompt: str = ""

    @classmethod
    def success(cls, content: str, **data: Any) -> SkillResult:
        return cls(ok=True, content=content, data=data)

    @classmethod
    def failure(cls, error: str) -> SkillResult:
        return cls(ok=False, content="", error=error)

    @classmethod
    def needs_confirmation(cls, prompt: str) -> SkillResult:
        return cls(
            ok=False,
            confirmation_required=True,
            confirmation_prompt=prompt,
            error="Confirmation required",
        )


@dataclass
class SkillContext:
    """Everything a skill may legitimately touch during one invocation.

    Note there is no filesystem root, no shell and no arbitrary network handle
    here: a skill only gets what the runtime explicitly injects. Network and
    credential access must go through :meth:`ensure_network` and
    :meth:`credential`, which enforce the per-skill capability grants.
    """

    jid: str
    capabilities: CapabilitySet = field(default_factory=CapabilitySet)
    redactor: Redactor | None = None
    room_jid: str | None = None
    session_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    #: Effective grants for the skill currently executing (per-skill scope).
    grants: list[CapabilityGrant] = field(default_factory=list)
    #: Credential store, consulted only through :meth:`credential`.
    credential_store: CredentialStore | None = None
    #: Name of the skill currently executing (for diagnostics).
    skill_name: str | None = None

    def redact(self, text: str) -> str:
        return self.redactor.redact(text) if self.redactor else text

    def _effective_grants(self) -> list[CapabilityGrant]:
        return self.grants or self.capabilities.grants

    def ensure_network(self, url: str) -> None:
        """Raise unless the current skill is allowed to reach *url*'s host."""
        from agent.security.network_guard import ensure_network_allowed

        ensure_network_allowed(url, grants=self._effective_grants())

    def credential(self, name: str, *, required: bool = True) -> str | None:
        """Resolve a named credential the current skill is permitted to use."""
        if self.credential_store is None:
            raise AuthorizationError(f"Credential '{name}' is not available here")
        from agent.security.network_guard import CredentialGuard

        return CredentialGuard(self._effective_grants(), self.credential_store).get(
            name, required=required
        )


@runtime_checkable
class Skill(Protocol):
    """Universal skill interface."""

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    def parameters_schema(self) -> dict[str, Any]: ...

    def capabilities(self) -> list[str]: ...

    def to_tool_spec(self) -> ToolSpec: ...

    async def execute(self, arguments: dict[str, Any], context: SkillContext) -> SkillResult: ...


class BaseSkill:
    """Convenience base implementing the boilerplate parts of :class:`Skill`."""

    #: Override in subclasses.
    skill_name: str = ""
    skill_description: str = ""
    #: JSON Schema object describing the accepted arguments.
    schema: dict[str, Any] = {"type": "object", "properties": {}}
    #: Capability names this skill requires.
    required_capabilities: list[str] = []

    @property
    def name(self) -> str:
        if not self.skill_name:
            raise ValueError(f"{type(self).__name__} must define skill_name")
        return self.skill_name

    @property
    def description(self) -> str:
        return self.skill_description

    def parameters_schema(self) -> dict[str, Any]:
        return self.schema

    def capabilities(self) -> list[str]:
        return self.required_capabilities

    def to_tool_spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            parameters=self.parameters_schema(),
        )

    async def execute(
        self, arguments: dict[str, Any], context: SkillContext
    ) -> SkillResult:  # pragma: no cover - abstract
        raise NotImplementedError
