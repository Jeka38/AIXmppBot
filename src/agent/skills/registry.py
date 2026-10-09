"""Skill registry and the authorization-first execution pipeline.

Execution order is fixed (SPEC §65)::

    schema validation → authorization → capability check
        → confirmation (if required) → execute → result sanitization

Nothing here trusts the LLM: the registry receives a name plus arguments and
re-derives every permission itself.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from agent.errors import AuthorizationError, SkillError
from agent.llm.base import ToolSpec
from agent.security.capabilities import CapabilityGrant, CapabilitySet
from agent.security.credentials import Redactor
from agent.security.policy import CapabilityPolicy
from agent.skills.base import Skill, SkillContext, SkillResult
from agent.skills.validation import validate_arguments

log = logging.getLogger(__name__)


@dataclass
class ConfirmationPolicy:
    """Which skills require explicit user confirmation before execution."""

    required: dict[str, bool] = field(default_factory=dict)
    #: Optional human-readable prompts per skill. Falls back to a generic
    #: message that includes the skill name and its arguments.
    prompts: dict[str, str] = field(default_factory=dict)

    def needs_confirmation(self, skill_name: str) -> bool:
        return bool(self.required.get(skill_name, False))

    def prompt_for(self, skill_name: str, arguments: dict[str, Any]) -> str:
        template = self.prompts.get(skill_name)
        if template:
            return template
        from agent.i18n import get_translator

        rendered = ", ".join(f"{key}={value!r}" for key, value in arguments.items())
        details = rendered or "—"
        return get_translator().t("confirmation.prompt", skill=skill_name, details=details)


@dataclass
class SkillRegistration:
    skill: Skill
    #: Granted capabilities for this skill. Derived from its declaration unless
    #: a caller narrows it further.
    grants: list[CapabilityGrant] = field(default_factory=list)
    enabled: bool = True

    @property
    def name(self) -> str:
        return self.skill.name


class SkillRegistry:
    """Holds skills, exposes tool specs, and executes with full guards."""

    def __init__(
        self,
        *,
        redactor: Redactor | None = None,
        confirmations: ConfirmationPolicy | None = None,
        policy: CapabilityPolicy | None = None,
    ) -> None:
        self.redactor = redactor or Redactor()
        self.confirmations = confirmations or ConfirmationPolicy()
        self.policy = policy or CapabilityPolicy.from_config({})
        self._registrations: dict[str, SkillRegistration] = {}

    # -- registration ----------------------------------------------------

    def register(
        self,
        skill: Skill,
        *,
        grants: list[CapabilityGrant] | None = None,
        enabled: bool = True,
    ) -> None:
        name = skill.name
        if not name:
            raise SkillError("Skill must have a non-empty name")
        if name in self._registrations:
            raise SkillError(f"Skill already registered: {name}")
        if grants is None:
            grants = self.policy.effective_grants(name, skill.capabilities())
        self._registrations[name] = SkillRegistration(skill=skill, grants=grants, enabled=enabled)

    @staticmethod
    def _declared_grants(skill: Skill) -> list[CapabilityGrant]:
        return [CapabilityGrant.parse(name) for name in skill.capabilities()]

    def unregister(self, name: str) -> None:
        self._registrations.pop(name, None)

    def get(self, name: str) -> SkillRegistration | None:
        return self._registrations.get(name)

    def all(self) -> list[SkillRegistration]:
        return list(self._registrations.values())

    def set_enabled(self, name: str, enabled: bool) -> None:
        registration = self._registrations.get(name)
        if registration is None:
            raise SkillError(f"Unknown skill: {name}")
        registration.enabled = enabled

    # -- tool definitions ------------------------------------------------

    def tool_definitions(self, *, only: set[str] | None = None) -> list[ToolSpec]:
        specs: list[ToolSpec] = []
        for registration in self._registrations.values():
            if not registration.enabled:
                continue
            if only is not None and registration.name not in only:
                continue
            specs.append(registration.skill.to_tool_spec())
        return specs

    # -- execution -------------------------------------------------------

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        context: SkillContext,
        *,
        confirmations_required: set[str] | None = None,
    ) -> SkillResult:
        """Run a skill through the full security pipeline.

        ``confirmations_required`` lists skills the caller has already obtained
        confirmation for. A skill configured to require confirmation and absent
        from this set is refused (the runtime must ask the user first).
        """
        registration = self._registrations.get(name)
        if registration is None:
            return SkillResult.failure(f"Unknown skill: {name}")
        if not registration.enabled:
            return SkillResult.failure(f"Skill is disabled: {name}")

        try:
            validate_arguments(registration.skill.parameters_schema(), arguments)
        except SkillError as exc:
            return SkillResult.failure(f"Invalid arguments for {name}: {exc}")

        try:
            self._authorize(registration, context)
        except AuthorizationError as exc:
            log.warning("Skill '%s' denied for %s: %s", name, context.jid, exc)
            return SkillResult.failure(f"Not permitted: {exc}")

        if self.confirmations.needs_confirmation(name):
            confirmed = confirmations_required or set()
            if name not in confirmed:
                return SkillResult.needs_confirmation(
                    self.confirmations.prompt_for(name, arguments)
                )

        context.grants = list(registration.grants)
        context.skill_name = name
        try:
            result = await registration.skill.execute(arguments, context)
        except AuthorizationError as exc:
            log.warning("Skill '%s' denied mid-execution: %s", name, exc)
            return SkillResult.failure(f"Not permitted: {exc}")
        except Exception as exc:  # noqa: BLE001 - skills must not kill the session
            log.exception("Skill '%s' raised", name)
            return SkillResult.failure(context.redact(f"Skill '{name}' failed: {exc}"))
        return self._sanitize(result, context)

    def _authorize(self, registration: SkillRegistration, context: SkillContext) -> None:
        # The registry owns the authoritative per-skill grants. A skill is
        # allowed when every capability it *declares* is present in its
        # effective grants; target-level enforcement (host/credential) happens
        # at call time via SkillContext.ensure_network / .credential.
        if not context.jid:
            raise AuthorizationError("Anonymous callers may not run skills")
        granted_caps = {grant.capability.value for grant in registration.grants}
        for name in registration.skill.capabilities():
            if name not in granted_caps:
                raise AuthorizationError(
                    f"Skill '{registration.name}' was not granted capability '{name}'"
                )

    def _sanitize(self, result: SkillResult, context: SkillContext) -> SkillResult:
        return SkillResult(
            ok=result.ok,
            content=context.redact(result.content),
            data=result.data,
            error=context.redact(result.error) if result.error else None,
        )


def granted_capabilities(skill: Skill) -> CapabilitySet:
    """Build the capability set a standalone skill would run with."""
    return CapabilitySet(grants=[CapabilityGrant.parse(name) for name in skill.capabilities()])
