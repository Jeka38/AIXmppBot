"""Model router: maps tiers to providers/models and exposes a chat facade.

Provider/model strings use ``provider:model`` (e.g. ``openai:gpt-4o``). A bare
string without a colon is treated as a model for the default provider.
Credentials and base URLs come from the ``[llm]`` config; a provider prefix
other than the configured one falls back to that provider's defaults and, where
relevant, its conventional environment variable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from agent.config.models import LLMConfig
from agent.errors import ConfigError, LLMError
from agent.llm.anthropic import AnthropicClient
from agent.llm.base import ChatMessage, LLMClient, LLMResponse, ToolSpec
from agent.llm.ollama import OllamaClient
from agent.llm.openai import OpenAIClient

_KNOWN_PROVIDERS = {"openai", "anthropic", "ollama"}

_DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "ollama": "http://127.0.0.1:11434",
}

_DEFAULT_ENV_KEYS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


@dataclass(frozen=True)
class ModelRef:
    provider: str
    model: str

    @classmethod
    def parse(cls, value: str, *, default_provider: str) -> ModelRef:
        value = value.strip()
        if not value:
            return cls(provider=default_provider, model="")
        if ":" in value:
            provider, _, model = value.partition(":")
            provider = provider.strip().lower()
            if provider in _KNOWN_PROVIDERS:
                return cls(provider=provider, model=model.strip())
        return cls(provider=default_provider, model=value)


class LLMRouter:
    """Owns provider clients and resolves tier requests to model calls."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self._clients: dict[str, LLMClient] = {}

    # -- tier resolution -------------------------------------------------

    def resolve(self, tier: str | None, model_override: str | None = None) -> tuple[LLMClient, str]:
        """Return ``(client, model)`` for the requested tier/override."""
        model_id = model_override or self._tier_model(tier)
        ref = ModelRef.parse(model_id, default_provider=self.config.provider)
        if not ref.model:
            raise ConfigError(
                f"No model configured for tier {tier!r}; set [llm.tiers] or llm.model."
            )
        return self._client_for(ref.provider), ref.model

    def _tier_model(self, tier: str | None) -> str:
        if tier:
            value = getattr(self.config.tiers, tier, "")
            if value:
                return value
        return self.config.model

    # -- provider clients ------------------------------------------------

    def _client_for(self, provider: str) -> LLMClient:
        if provider in self._clients:
            return self._clients[provider]
        client = self._build_client(provider)
        self._clients[provider] = client
        return client

    def _build_client(self, provider: str) -> LLMClient:
        if provider == "openai":
            return OpenAIClient(
                base_url=self._base_url(provider),
                api_key=self._credential(provider),
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
                timeout=self.config.request_timeout,
            )
        if provider == "anthropic":
            return AnthropicClient(
                base_url=self._base_url(provider),
                api_key=self._credential(provider),
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
                timeout=self.config.request_timeout,
            )
        if provider == "ollama":
            return OllamaClient(
                base_url=self._base_url(provider),
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
                timeout=self.config.request_timeout,
            )
        raise ConfigError(f"Unsupported LLM provider: {provider!r}")

    def _base_url(self, provider: str) -> str:
        if provider == self.config.provider and self.config.base_url:
            return self.config.base_url
        return _DEFAULT_BASE_URLS[provider]

    def _credential(self, provider: str) -> str:
        if provider == self.config.provider and self.config.api_key:
            return self.config.api_key
        env_key = _DEFAULT_ENV_KEYS.get(provider)
        return os.environ.get(env_key, "") if env_key else ""

    # -- facade ----------------------------------------------------------

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        tier: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        client, resolved_model = self.resolve(tier, model)
        try:
            return await client.chat(
                messages,
                tools=tools,
                model=resolved_model,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except LLMError:
            raise

    async def aclose(self) -> None:
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()
