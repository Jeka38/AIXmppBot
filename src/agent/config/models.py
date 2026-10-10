"""Pydantic configuration models.

The configuration surface is intentionally declarative and validated on load.
Environment variables are expanded before parsing (see :mod:`agent.config.loader`).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class XMPPConfig(BaseModel):
    """Connection settings for either C2S or component mode."""

    mode: Literal["c2s", "component"] = "c2s"

    # C2S
    jid: str = ""
    password: str = ""

    # Component (XEP-0114)
    component_jid: str = ""
    secret: str = ""

    # Shared transport
    host: str = ""
    port: int = 0
    starttls: bool = True
    verify_tls: bool = True
    resource: str = "ai-agent"
    nickname: str = "AI-Agent"

    # Reconnection
    reconnect: bool = True
    reconnect_max_seconds: float = 300.0
    reconnect_base_seconds: float = 1.0

    # Keepalive / timeouts
    connection_timeout: float = 30.0

    def effective_jid(self) -> str:
        """Return the JID this transport authenticates as."""
        return self.component_jid if self.mode == "component" else self.jid


class SecurityConfig(BaseModel):
    """Authorization and safety policy."""

    allowed_jids: list[str] = Field(default_factory=list)
    allowed_domains: list[str] = Field(default_factory=list)
    allow_wildcard: bool = False

    dev_command_jids: list[str] = Field(default_factory=list)
    allow_raw_xml: bool = False

    #: JIDs (bare or full), domains or MUC nicks whose messages are always
    #: ignored: no reply, no commands, no roster subscription. Entries with an
    #: ``@`` are matched as JIDs, plain entries as domains or MUC nicks.
    ignored_jids: list[str] = Field(default_factory=list)
    #: MUC nicks to ignore regardless of room.
    ignored_nicks: list[str] = Field(default_factory=list)

    #: Automatically approve roster subscription requests from senders that pass
    #: the allowlist (and are not ignored). Denied for everyone else, so the bot
    #: never silently accepts unknown contacts.
    auto_authorize_subscriptions: bool = True

    require_tls: bool = True

    confirmations: dict[str, bool] = Field(default_factory=dict)

    @field_validator(
        "allowed_jids",
        "allowed_domains",
        "dev_command_jids",
        "ignored_jids",
        "ignored_nicks",
        mode="before",
    )
    @classmethod
    def _coerce_entries(cls, value: object) -> list[str]:
        """Accept either a TOML list or a comma-separated string.

        The string form lets values come from environment variables, e.g.
        ``dev_command_jids = "${ADMIN_JIDS:-}"`` with
        ``ADMIN_JIDS=alice@example.com,bob@example.com``.
        """
        if isinstance(value, str):
            items = value.replace("\n", ",").split(",")
        elif isinstance(value, (list, tuple, set)):
            items = [str(item) for item in value]
        else:
            return []
        return [item.strip().lower() for item in items if item.strip()]


class RoomConfig(BaseModel):
    """Per-room MUC behaviour."""

    jid: str
    nick: str = "AI-Agent"
    activation: Literal["mention", "all"] = "mention"
    aliases: list[str] = Field(default_factory=list)
    history_token_budget: int = 80000

    # Optional per-room allowlist. Empty means fall back to global security.
    allowed_jids: list[str] = Field(default_factory=list)

    #: When true, only senders whose real JID is in an allowlist may use the
    #: bot; anonymous occupants (their real JID hidden by the server) are
    #: ignored. Default false: any occupant of an explicitly configured room may
    #: talk to the bot, because the room itself is a trusted scope.
    restrict_to_allowed_jids: bool = False

    password: str = ""


class LLMTierConfig(BaseModel):
    """Model identifiers per capability tier (``provider:model``)."""

    light: str = ""
    standard: str = ""
    heavy: str = ""
    vision: str = ""


class LLMConfig(BaseModel):
    provider: Literal["openai", "anthropic", "ollama"] = "openai"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    max_tokens: int = 8192
    temperature: float = 0.7
    request_timeout: float = 120.0

    tiers: LLMTierConfig = Field(default_factory=LLMTierConfig)

    dm_history_token_budget: int = 50000
    muc_history_token_budget: int = 80000

    system_prompt: str = ""


class AgentConfig(BaseModel):
    """Core agent-loop behaviour."""

    max_tool_rounds: int = 10
    name: str = "AI Agent"
    identity_file: str = ""
    #: UI language for command replies and generated prompts ("en" or "ru").
    language: str = "en"


class QuotaConfig(BaseModel):
    enabled: bool = False
    daily_tokens: int = 100000
    per_user_daily_tokens: int = 20000
    on_exceed: Literal["reject", "fallback_light"] = "reject"


class PersonaConfig(BaseModel):
    allow_user_personas: bool = True
    max_user_personas: int = 5
    allow_sharing: bool = True
    default_persona: str = "general-assistant"


class AttachmentsConfig(BaseModel):
    enabled: bool = True
    max_size_mb: int = 25
    download_timeout: float = 30.0
    temp_dir: str = ""


class SkillsConfig(BaseModel):
    """Root skill settings; individual skill tables are nested mappings."""

    enabled: list[str] = Field(default_factory=list)
    web_search: dict[str, Any] = Field(default_factory=dict)
    github: dict[str, Any] = Field(default_factory=dict)
    url_fetch: dict[str, Any] = Field(default_factory=dict)

    #: Per-skill capability policy. When set for a skill, it is authoritative
    #: and replaces that skill's declared capabilities (SPEC §31). Example::

    #:     [skills.capabilities.github]
    #:     network = ["api.github.com:443"]
    #:     credential = ["github-token"]
    capabilities: dict[str, dict[str, list[str]]] = Field(default_factory=dict)


class StorageConfig(BaseModel):
    backend: Literal["filesystem", "postgres"] = "filesystem"
    data_dir: str = "data"


class LoggingConfig(BaseModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    json_logs: bool = False


class AppConfig(BaseModel):
    """Top-level validated configuration."""

    xmpp: XMPPConfig = Field(default_factory=XMPPConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    quotas: QuotaConfig = Field(default_factory=QuotaConfig)
    personas: PersonaConfig = Field(default_factory=PersonaConfig)
    attachments: AttachmentsConfig = Field(default_factory=AttachmentsConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    rooms: list[RoomConfig] = Field(default_factory=list)

    def redacted(self) -> dict[str, Any]:
        """Return a JSON-safe view with secrets removed (for logs/startup)."""
        data = self.model_dump()
        for section in ("xmpp", "llm", "security"):
            block = data.get(section)
            if not isinstance(block, dict):
                continue
            for key in ("password", "secret", "api_key"):
                if block.get(key):
                    block[key] = "***"
        return data
