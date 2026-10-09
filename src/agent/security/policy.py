"""Per-skill capability policy (SPEC §31).

The deployment declares, per skill, which capabilities are granted. If a skill
has no explicit policy entry it falls back to its own declaration (so built-in
skills keep working out of the box). When a policy entry exists it is
authoritative and must cover the skill's declaration — otherwise registration
fails loudly rather than silently under-granting.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.errors import SkillError
from agent.security.capabilities import CapabilityGrant


@dataclass(frozen=True)
class CapabilityPolicy:
    #: skill name -> capability name -> list of targets
    spec: dict[str, dict[str, list[str]]]

    @classmethod
    def from_config(cls, spec: dict[str, dict[str, list[str]]] | None) -> CapabilityPolicy:
        return cls(spec=dict(spec or {}))

    def effective_grants(self, skill_name: str, declared: list[str]) -> list[CapabilityGrant]:
        """Return the grants a skill runs with.

        A configured entry *replaces* the declaration; otherwise the declared
        capabilities are used unscoped.
        """
        entry = self.spec.get(skill_name)
        if entry is None:
            return [CapabilityGrant.parse(name) for name in declared]
        grants: list[CapabilityGrant] = []
        for capability, targets in entry.items():
            grants.append(CapabilityGrant.parse(capability, targets))
        self._verify_covers(skill_name, declared, grants)
        return grants

    @staticmethod
    def _verify_covers(
        skill_name: str,
        declared: list[str],
        grants: list[CapabilityGrant],
    ) -> None:
        granted_caps = {grant.capability.value for grant in grants}
        for name in declared:
            if name not in granted_caps:
                raise SkillError(
                    f"Capability policy for skill '{skill_name}' does not grant "
                    f"required capability '{name}'."
                )
