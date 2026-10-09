"""Credential storage and secret redaction (SPEC §33).

Secrets are never placed in prompts, history, logs or memory. Two mechanisms
enforce this:

* :class:`CredentialStore` resolves named secrets from the environment (and,
  optionally, an encrypted store in a later phase).
* :class:`Redactor` registers known secret values and scrubs them from any text
  before it reaches logs, the LLM, or persisted history.
"""

from __future__ import annotations

import os
from typing import Protocol

from agent.errors import AuthenticationError

_MIN_SECRET_LEN = 4


class CredentialStore(Protocol):
    """Look up named secrets without exposing them to the runtime broadly."""

    def get(self, name: str) -> str | None: ...

    def require(self, name: str) -> str: ...

    def names(self) -> list[str]: ...


class EnvCredentialStore:
    """Resolve credentials from environment variables.

    Lookup order for name ``github-token``: ``GITHUB_TOKEN`` (upper, dashes to
    underscores) then the literal name.
    """

    def __init__(self, prefixes: tuple[str, ...] = ()) -> None:
        self.prefixes = prefixes

    @staticmethod
    def _env_name(name: str) -> str:
        return name.upper().replace("-", "_").replace(".", "_")

    def get(self, name: str) -> str | None:
        candidates = [self._env_name(name), name]
        if self.prefixes:
            prefixed = [f"{p.upper()}_{c}" for p in self.prefixes for c in candidates]
            candidates = prefixed + candidates
        for candidate in candidates:
            value = os.environ.get(candidate)
            if value:
                return value
        return None

    def require(self, name: str) -> str:
        value = self.get(name)
        if not value:
            raise AuthenticationError(f"Credential '{name}' is not available in the environment")
        return value

    def names(self) -> list[str]:
        return sorted(os.environ)


class Redactor:
    """Scrub registered secret values from arbitrary text."""

    def __init__(self) -> None:
        self._secrets: set[str] = set()

    def register(self, *values: str | None) -> None:
        for value in values:
            if value and len(value) >= _MIN_SECRET_LEN:
                self._secrets.add(value)

    def redact(self, text: str) -> str:
        if not text:
            return text
        result = text
        # Replace longest secrets first to avoid partial masking.
        for secret in sorted(self._secrets, key=len, reverse=True):
            if secret in result:
                result = result.replace(secret, "***")
        return result

    def redact_many(self, texts: list[str]) -> list[str]:
        return [self.redact(text) for text in texts]

    @property
    def has_secrets(self) -> bool:
        return bool(self._secrets)
