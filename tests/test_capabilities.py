"""Per-skill capability enforcement, network guard and credential guard (SPEC §31)."""

from __future__ import annotations

import pytest

from agent.errors import AuthorizationError
from agent.security.capabilities import (
    Capability,
    CapabilityGrant,
    CapabilitySet,
    normalize_network_target,
)
from agent.security.credentials import EnvCredentialStore
from agent.security.network_guard import (
    CredentialGuard,
    ensure_network_allowed,
    network_target_for_url,
)
from agent.security.policy import CapabilityPolicy
from agent.skills.base import BaseSkill, SkillContext, SkillResult
from agent.skills.registry import SkillRegistry

# -- target normalisation ----------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("api.github.com", "api.github.com:443"),
        ("api.github.com:443", "api.github.com:443"),
        ("http://example.com/path", "example.com:80"),
        ("https://example.com/path", "example.com:443"),
        ("https://example.com:8443/x", "example.com:8443"),
        ("[::1]:9000", "[::1]:9000"),
    ],
)
def test_normalize_network_target(raw: str, expected: str) -> None:
    assert normalize_network_target(raw) == expected


def test_network_target_for_url() -> None:
    assert network_target_for_url("https://api.github.com/repos") == "api.github.com:443"
    assert network_target_for_url("http://example.com:8080/x") == "example.com:8080"


# -- network guard -----------------------------------------------------------


def _grants(*specs: tuple[str, list[str]]) -> list[CapabilityGrant]:
    return [CapabilityGrant.parse(cap, targets) for cap, targets in specs]


def test_network_guard_allows_scoped_host() -> None:
    grants = _grants(("network", ["api.github.com:443"]))
    ensure_network_allowed("https://api.github.com/x", grants=grants)


def test_network_guard_blocks_other_host() -> None:
    grants = _grants(("network", ["api.github.com:443"]))
    with pytest.raises(AuthorizationError):
        ensure_network_allowed("https://evil.example.com/x", grants=grants)


def test_network_guard_blocks_wrong_port() -> None:
    grants = _grants(("network", ["api.github.com:443"]))
    with pytest.raises(AuthorizationError):
        ensure_network_allowed("https://api.github.com:8443/x", grants=grants)


def test_network_guard_unrestricted_grant_allows_all() -> None:
    grants = _grants(("network", []))
    ensure_network_allowed("https://anywhere.example.com/x", grants=grants)


def test_network_guard_blocks_without_network_grant() -> None:
    grants = _grants(("credential", ["github-token"]))
    with pytest.raises(AuthorizationError):
        ensure_network_allowed("https://api.github.com/x", grants=grants)


# -- credential guard --------------------------------------------------------


def test_credential_guard_requires_grant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    grants = _grants(("credential", ["github-token"]))
    guard = CredentialGuard(grants, EnvCredentialStore())
    assert guard.get("github-token") == "ghp_secret"


def test_credential_guard_denies_ungranted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    guard = CredentialGuard(_grants(("network", [])), EnvCredentialStore())
    with pytest.raises(AuthorizationError):
        guard.get("github-token")


def test_credential_guard_missing_value_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.errors import AuthenticationError

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    guard = CredentialGuard(_grants(("credential", ["github-token"])), EnvCredentialStore())
    with pytest.raises(AuthenticationError):
        guard.get("github-token")


# -- SkillContext helpers ----------------------------------------------------


def test_context_ensure_network_uses_effective_grants() -> None:
    context = SkillContext(
        jid="alice@example.com",
        grants=_grants(("network", ["api.github.com:443"])),
    )
    context.ensure_network("https://api.github.com/x")
    with pytest.raises(AuthorizationError):
        context.ensure_network("https://evil.example.com/x")


def test_context_credential_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    context = SkillContext(
        jid="alice@example.com",
        grants=_grants(("credential", ["github-token"])),
        credential_store=EnvCredentialStore(),
    )
    assert context.credential("github-token") == "ghp_secret"
    with pytest.raises(AuthorizationError):
        context.credential("other-token")


# -- policy production behaviour ---------------------------------------------


class ScopedSkill(BaseSkill):
    skill_name = "scoped"
    skill_description = "Needs a specific host."
    schema = {"type": "object", "properties": {}}
    required_capabilities = ["network"]

    async def execute(self, arguments, context):
        context.ensure_network("https://api.github.com/x")
        return SkillResult.success("ok")


async def test_scoped_skill_runs_with_matching_policy() -> None:
    policy = CapabilityPolicy.from_config({"scoped": {"network": ["api.github.com:443"]}})
    registry = SkillRegistry(policy=policy)
    registry.register(ScopedSkill())
    result = await registry.execute("scoped", {}, SkillContext(jid="alice@example.com"))
    assert result.ok


async def test_scoped_skill_denied_other_host() -> None:
    policy = CapabilityPolicy.from_config({"scoped": {"network": ["other.example.com:443"]}})
    registry = SkillRegistry(policy=policy)
    registry.register(ScopedSkill())
    result = await registry.execute("scoped", {}, SkillContext(jid="alice@example.com"))
    assert not result.ok
    assert "Not permitted" in (result.error or "")


def test_capability_set_check_raises() -> None:
    with pytest.raises(AuthorizationError):
        CapabilitySet(grants=_grants(("state", []))).check(
            CapabilityGrant.parse(Capability.NETWORK.value)
        )
