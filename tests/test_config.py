"""Configuration loading, env expansion and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.config.loader import expand_env, load_config, validate_config
from agent.errors import ConfigError

VALID = """
[xmpp]
mode = "c2s"
jid = "agent@example.com"
password = "secret"

[security]
allowed_domains = ["example.com"]

[llm]
provider = "openai"
model = "test-model"
"""


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "agent.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_load_minimal_config(tmp_path: Path) -> None:
    config = validate_config(_write(tmp_path, VALID))
    assert config.xmpp.jid == "agent@example.com"
    assert config.security.allowed_domains == ["example.com"]


def test_env_expansion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XMPP_JID", "env@example.com")
    monkeypatch.setenv("XMPP_PASSWORD", "envsecret")
    body = VALID.replace('"agent@example.com"', '"${XMPP_JID}"').replace(
        '"secret"', '"${XMPP_PASSWORD}"'
    )
    config = load_config(_write(tmp_path, body))
    assert config.xmpp.jid == "env@example.com"
    assert config.xmpp.password == "envsecret"


def test_env_default_value() -> None:
    expanded = expand_env("${MISSING_VAR:-fallback}")
    assert expanded == "fallback"


def test_missing_env_without_default_raises() -> None:
    with pytest.raises(ConfigError):
        expand_env("${DEFINITELY_MISSING_VAR_XYZ}")


def test_missing_allowlist_rejected(tmp_path: Path) -> None:
    body = VALID.replace('allowed_domains = ["example.com"]', "")
    with pytest.raises(ConfigError, match="allowed_jids|allowed_domains"):
        validate_config(_write(tmp_path, body))


def test_wildcard_requires_opt_in(tmp_path: Path) -> None:
    body = VALID.replace('["example.com"]', '["*"]')
    with pytest.raises(ConfigError, match="allow_wildcard"):
        validate_config(_write(tmp_path, body))
    body_with_flag = body.replace("[security]", "[security]\nallow_wildcard = true")
    config = validate_config(_write(tmp_path, body_with_flag))
    assert config.security.allow_wildcard is True


def test_c2s_requires_credentials(tmp_path: Path) -> None:
    body = VALID.replace('password = "secret"', 'password = ""')
    with pytest.raises(ConfigError, match="c2s"):
        validate_config(_write(tmp_path, body))


def test_component_requires_secret(tmp_path: Path) -> None:
    body = """
[xmpp]
mode = "component"
component_jid = "agent.example.com"

[security]
allowed_domains = ["example.com"]
"""
    with pytest.raises(ConfigError, match="component"):
        validate_config(_write(tmp_path, body))


def test_redacted_hides_secrets(config) -> None:
    redacted = config.redacted()
    assert redacted["llm"]["api_key"] == "***"
    assert redacted["xmpp"]["password"] == "***"


def test_dev_command_jids_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_JIDS", "alice@example.com, bob@example.com ")
    monkeypatch.setenv("ALLOWED_JIDS", "alice@example.com")
    section = '[security]\ndev_command_jids = "${ADMIN_JIDS:-}"'
    body = VALID.replace("[security]", section)
    body = body.replace(
        'allowed_domains = ["example.com"]',
        'allowed_domains = ["example.com"]\nallowed_jids = "${ALLOWED_JIDS:-}"',
    )
    config = load_config(_write(tmp_path, body))
    assert config.security.dev_command_jids == ["alice@example.com", "bob@example.com"]
    assert config.security.allowed_jids == ["alice@example.com"]


def test_string_lists_accept_toml_and_csv(tmp_path: Path) -> None:
    from agent.config.models import SecurityConfig

    as_list = SecurityConfig(dev_command_jids=["A@Example.com", "b@example.com"])
    as_csv = SecurityConfig(dev_command_jids="A@Example.com, b@example.com")
    assert (
        as_list.dev_command_jids
        == as_csv.dev_command_jids
        == [
            "a@example.com",
            "b@example.com",
        ]
    )


def test_empty_env_admin_list_is_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.config.models import SecurityConfig

    monkeypatch.delenv("ADMIN_JIDS", raising=False)
    expanded = expand_env("${ADMIN_JIDS:-}")
    assert expanded == ""
    assert SecurityConfig(dev_command_jids=expanded).dev_command_jids == []
