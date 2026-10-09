"""Typed exception hierarchy for the agent.

Every failure surfaced to a user must map to one of the categories defined
here so the runtime can present a clear message while logging full detail.
"""

from __future__ import annotations


class AgentError(Exception):
    """Base class for all agent errors."""


class ConfigError(AgentError):
    """Invalid or missing configuration."""


class XMPPError(AgentError):
    """Transport-level XMPP failure."""


class LLMError(AgentError):
    """LLM provider failure."""


class SkillError(AgentError):
    """A skill failed while executing."""


class AuthenticationError(AgentError):
    """Credentials could not be established."""


class AuthorizationError(AgentError):
    """The sender is not permitted to perform the requested action."""


class TimeoutError_(AgentError):
    """An operation exceeded its deadline.

    Named with a trailing underscore to avoid shadowing the builtin
    :class:`TimeoutError` while keeping a uniform ``AgentError`` hierarchy.
    """


class StorageError(AgentError):
    """Persistent storage could not be read or written."""


class SecurityError(AgentError):
    """A security policy was violated (injection, SSRF, redaction, ...)."""


class QuotaError(AgentError):
    """A usage quota was exceeded."""
