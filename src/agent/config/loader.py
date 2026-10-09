"""Configuration loader with ``${ENV_VAR}`` expansion.

Precedence (highest first):
1. Process environment variables.
2. Values from a ``.env`` file next to the config (loaded, but never committed).
3. Literal values in the TOML file.

Expansion supports ``${VAR}`` and ``${VAR:-default}``. Unknown variables without
a default raise :class:`ConfigError` so misconfiguration fails loudly at startup.
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agent.config.models import AppConfig
from agent.errors import ConfigError

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def load_dotenv(path: Path, *, override: bool = False) -> None:
    """Load ``KEY=VALUE`` pairs from *path* into ``os.environ``.

    Minimal parser: ignores blank lines and ``#`` comments, strips optional
    surrounding quotes. Does not support multi-line values.
    """
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if override or key not in os.environ:
            os.environ[key] = value


def expand_env(value: Any, *, source: str = "<config>") -> Any:
    """Recursively expand ``${VAR}`` references in strings, lists and dicts."""
    if isinstance(value, str):
        return _expand_string(value, source=source)
    if isinstance(value, list):
        return [expand_env(item, source=source) for item in value]
    if isinstance(value, dict):
        return {key: expand_env(item, source=source) for key, item in value.items()}
    return value


def _expand_string(value: str, *, source: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        default = match.group(2)
        if name in os.environ:
            return os.environ[name]
        if default is not None:
            return default
        raise ConfigError(
            f"Environment variable '{name}' referenced in {source} is not set and has no default."
        )

    return _ENV_PATTERN.sub(_replace, value)


def load_config(path: str | Path, *, load_env_file: bool = True) -> AppConfig:
    """Parse, expand and validate a TOML configuration file."""
    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(f"Configuration file not found: {config_path}")

    if load_env_file:
        load_dotenv(config_path.parent / ".env")
        load_dotenv(Path.cwd() / ".env")

    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {config_path}: {exc}") from exc

    expanded = expand_env(raw, source=str(config_path))
    try:
        return AppConfig.model_validate(expanded)
    except ValidationError as exc:
        raise ConfigError(f"Invalid configuration in {config_path}:\n{exc}") from exc


def validate_config(path: str | Path) -> AppConfig:
    """Load and cross-validate a config, raising :class:`ConfigError` on issues."""
    config = load_config(path)
    _cross_validate(config)
    return config


def _cross_validate(config: AppConfig) -> None:
    xmpp = config.xmpp
    if xmpp.mode == "c2s":
        if not xmpp.jid or not xmpp.password:
            raise ConfigError("xmpp.mode=c2s requires both 'jid' and 'password'.")
    elif xmpp.mode == "component":
        if not xmpp.component_jid or not xmpp.secret:
            raise ConfigError("xmpp.mode=component requires both 'component_jid' and 'secret'.")
    else:  # pragma: no cover - guarded by the Literal type
        raise ConfigError(f"Unsupported xmpp.mode: {xmpp.mode!r}")

    if not config.security.allowed_jids and not config.security.allowed_domains:
        raise ConfigError(
            "security must define at least one of allowed_jids / allowed_domains; "
            "an empty policy would deny all senders."
        )

    if "*" in config.security.allowed_domains and not config.security.allow_wildcard:
        raise ConfigError(
            "allowed_domains contains '*' but security.allow_wildcard is false. "
            "Set allow_wildcard=true to explicitly enable this."
        )
