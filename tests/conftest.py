"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.config.models import (
    AgentConfig,
    AppConfig,
    LLMConfig,
    LLMTierConfig,
    SecurityConfig,
    StorageConfig,
    XMPPConfig,
)
from agent.llm.base import ChatMessage, LLMResponse, ToolCall, ToolSpec, Usage
from agent.llm.tokenizer import TokenEstimator
from agent.memory.store import DataLayout, FilesystemSessionStore
from agent.skills.registry import SkillRegistry


def make_config(**overrides: object) -> AppConfig:
    """Build a minimal valid config for tests."""
    security = SecurityConfig(
        allowed_jids=["alice@example.com", "bob@example.com", "admin@example.com"],
        allowed_domains=["example.com"],
        dev_command_jids=["admin@example.com"],
    )
    config = AppConfig(
        xmpp=XMPPConfig(mode="c2s", jid="agent@example.com", password="secret"),
        security=security,
        agent=AgentConfig(name="Test Agent"),
        llm=LLMConfig(
            provider="openai",
            base_url="http://localhost:9999/v1",
            api_key="test-key",
            model="test-model",
            tiers=LLMTierConfig(light="test-light", standard="test-model"),
        ),
        storage=StorageConfig(data_dir="data"),
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


class FakeLLMClient:
    """Scripted LLM client returning queued responses."""

    def __init__(self, responses: list[LLMResponse] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[list[ChatMessage]] = []
        self.tools_seen: list[list[ToolSpec] | None] = []
        self.closed = False

    def queue(self, response: LLMResponse) -> None:
        self.responses.append(response)

    def queue_text(self, text: str) -> None:
        self.responses.append(
            LLMResponse(content=text, usage=Usage(input_tokens=1, output_tokens=1))
        )

    def queue_tool(self, name: str, arguments: dict, call_id: str = "call_1") -> None:
        self.responses.append(
            LLMResponse(
                content="",
                tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
                usage=Usage(input_tokens=2, output_tokens=1),
            )
        )

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        self.calls.append(list(messages))
        self.tools_seen.append(tools)
        if not self.responses:
            return LLMResponse(
                content="(no scripted response)",
                usage=Usage(input_tokens=0, output_tokens=0),
            )
        return self.responses.pop(0)

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def config() -> AppConfig:
    return make_config()


@pytest.fixture
def estimator() -> TokenEstimator:
    return TokenEstimator("test-model")


@pytest.fixture
def layout(tmp_path: Path) -> DataLayout:
    return DataLayout(tmp_path / "data")


@pytest.fixture
def session_store(layout: DataLayout) -> FilesystemSessionStore:
    return FilesystemSessionStore(layout.memory_dir)


@pytest.fixture
def skills() -> SkillRegistry:
    return SkillRegistry()
