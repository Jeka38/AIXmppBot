"""Skill pipeline: validation, capabilities, confirmation, error isolation."""

from __future__ import annotations

import pytest

from agent.errors import AuthorizationError
from agent.security.capabilities import CapabilitySet
from agent.skills.base import BaseSkill, SkillContext, SkillResult
from agent.skills.registry import ConfirmationPolicy, SkillRegistry


class EchoSkill(BaseSkill):
    skill_name = "echo"
    skill_description = "Echo the input."
    schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    }

    async def execute(self, arguments, context):
        return SkillResult.success(arguments["text"])


class BoomSkill(BaseSkill):
    skill_name = "boom"
    skill_description = "Always raises."
    schema = {"type": "object", "properties": {}}

    async def execute(self, arguments, context):
        raise RuntimeError("kaboom")


class NetworkSkill(BaseSkill):
    skill_name = "net"
    skill_description = "Needs network."
    schema = {"type": "object", "properties": {}}
    required_capabilities = ["network"]

    async def execute(self, arguments, context):
        return SkillResult.success("fetched")


@pytest.fixture
def context() -> SkillContext:
    return SkillContext(jid="alice@example.com")


async def test_valid_arguments_execute(skills: SkillRegistry, context: SkillContext) -> None:
    skills.register(EchoSkill())
    result = await skills.execute("echo", {"text": "hi"}, context)
    assert result.ok
    assert result.content == "hi"


async def test_invalid_arguments_rejected(skills: SkillRegistry, context: SkillContext) -> None:
    skills.register(EchoSkill())
    result = await skills.execute("echo", {"text": 5}, context)
    assert not result.ok
    assert "Invalid arguments" in (result.error or "")


async def test_missing_required_argument_rejected(
    skills: SkillRegistry, context: SkillContext
) -> None:
    skills.register(EchoSkill())
    result = await skills.execute("echo", {}, context)
    assert not result.ok


async def test_unknown_property_rejected(skills: SkillRegistry, context: SkillContext) -> None:
    skills.register(EchoSkill())
    result = await skills.execute("echo", {"text": "hi", "extra": 1}, context)
    assert not result.ok


async def test_skill_error_isolated(skills: SkillRegistry, context: SkillContext) -> None:
    skills.register(BoomSkill())
    result = await skills.execute("boom", {}, context)
    assert not result.ok
    assert "kaboom" in (result.error or "")


async def test_capability_denied(skills: SkillRegistry, context: SkillContext) -> None:
    # A skill registered without covering grants cannot execute.
    skills.register(NetworkSkill(), grants=[])
    result = await skills.execute("net", {}, context)
    assert not result.ok
    assert "Not permitted" in (result.error or "")


async def test_capability_grant_policy_is_authoritative(skills: SkillRegistry) -> None:
    # No explicit policy: the declared capability is trusted.
    skills.register(NetworkSkill())
    context = SkillContext(jid="alice@example.com")
    result = await skills.execute("net", {}, context)
    assert result.ok


async def test_capability_narrowed_by_policy() -> None:
    from agent.security.policy import CapabilityPolicy

    policy = CapabilityPolicy.from_config({"net": {"network": ["api.example.com:443"]}})
    registry = SkillRegistry(policy=policy)
    registry.register(NetworkSkill())
    registration = registry.get("net")
    assert registration is not None
    targets = {t for grant in registration.grants for t in grant.targets}
    assert targets == {"api.example.com:443"}


async def test_policy_missing_declared_capability_rejected() -> None:
    from agent.errors import SkillError
    from agent.security.policy import CapabilityPolicy

    policy = CapabilityPolicy.from_config({"net": {"state": []}})
    registry = SkillRegistry(policy=policy)
    with pytest.raises(SkillError):
        registry.register(NetworkSkill())


async def test_disabled_skill_refused(skills: SkillRegistry, context: SkillContext) -> None:
    skills.register(EchoSkill(), enabled=False)
    result = await skills.execute("echo", {"text": "hi"}, context)
    assert not result.ok
    assert "disabled" in (result.error or "")


async def test_confirmation_required(skills: SkillRegistry, context: SkillContext) -> None:
    registry = SkillRegistry(confirmations=ConfirmationPolicy(required={"echo": True}))
    registry.register(EchoSkill())
    blocked = await registry.execute("echo", {"text": "hi"}, context)
    assert not blocked.ok
    assert blocked.confirmation_required
    assert "Confirm?" in blocked.confirmation_prompt

    confirmed = await registry.execute(
        "echo", {"text": "hi"}, context, confirmations_required={"echo"}
    )
    assert confirmed.ok


async def test_anonymous_caller_rejected(skills: SkillRegistry) -> None:
    skills.register(EchoSkill())
    result = await skills.execute("echo", {"text": "hi"}, SkillContext(jid=""))
    assert not result.ok


async def test_tool_definitions_respect_enabled(skills: SkillRegistry) -> None:
    skills.register(EchoSkill())
    skills.register(BoomSkill(), enabled=False)
    names = {spec.name for spec in skills.tool_definitions()}
    assert names == {"echo"}


def test_authorization_error_type() -> None:
    from agent.security.capabilities import CapabilityGrant

    with pytest.raises(AuthorizationError):
        CapabilitySet().check(CapabilityGrant.parse("network"))
