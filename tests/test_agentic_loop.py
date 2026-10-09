"""Agentic loop: tool rounds, limit handling and error isolation (SPEC §8)."""

from __future__ import annotations

from agent.llm.base import ChatMessage
from agent.runtime.loop import AgentLoop
from agent.security.capabilities import CapabilitySet
from agent.skills.base import BaseSkill, SkillContext, SkillResult
from agent.skills.registry import SkillRegistry


class AddSkill(BaseSkill):
    skill_name = "add"
    skill_description = "Add two integers."
    schema = {
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
    }

    async def execute(self, arguments, context):
        return SkillResult.success(str(arguments["a"] + arguments["b"]))


class BoomSkill(BaseSkill):
    skill_name = "boom"
    skill_description = "Raises."
    schema = {"type": "object", "properties": {}}

    async def execute(self, arguments, context):
        raise RuntimeError("broken")


class FakeRouter:
    def __init__(self, client) -> None:
        self.client = client

    async def chat(
        self,
        messages,
        *,
        tools=None,
        tier=None,
        model=None,
        max_tokens=None,
        temperature=None,
    ):
        return await self.client.chat(messages, tools=tools)

    async def aclose(self) -> None:
        await self.client.aclose()


def _loop(client, registry, **kwargs) -> AgentLoop:
    return AgentLoop(FakeRouter(client), registry, **kwargs)


async def _context() -> SkillContext:
    return SkillContext(jid="alice@example.com", capabilities=CapabilitySet())


async def test_single_tool_round() -> None:
    from tests.conftest import FakeLLMClient

    client = FakeLLMClient()
    client.queue_tool("add", {"a": 2, "b": 3})
    client.queue_text("The answer is 5.")

    registry = SkillRegistry()
    registry.register(AddSkill())
    loop = _loop(client, registry, max_tool_rounds=5)
    outcome = await loop.run([ChatMessage(role="user", content="2+3?")], await _context())

    assert outcome.content == "The answer is 5."
    assert outcome.rounds == 1
    assert len(outcome.tool_executions) == 1
    assert outcome.tool_executions[0].content == "5"
    assert not outcome.hit_tool_limit


async def test_tool_error_fed_back_to_llm() -> None:
    from tests.conftest import FakeLLMClient

    client = FakeLLMClient()
    client.queue_tool("boom", {})
    client.queue_text("The tool failed, sorry.")

    registry = SkillRegistry()
    registry.register(BoomSkill())
    loop = _loop(client, registry, max_tool_rounds=5)
    outcome = await loop.run([ChatMessage(role="user", content="go")], await _context())

    assert outcome.content == "The tool failed, sorry."
    assert outcome.tool_executions[0].ok is False
    # The error must have been delivered as a tool message.
    tool_messages = [message for call in client.calls for message in call if message.role == "tool"]
    assert any("broken" in message.content for message in tool_messages)


async def test_tool_round_limit_stops_tools() -> None:
    from tests.conftest import FakeLLMClient

    client = FakeLLMClient()
    # The model keeps requesting tools; the (max+1)-th request hits the limit.
    for _ in range(3):
        client.queue_tool("add", {"a": 1, "b": 1})
    client.queue_text("Final answer.")

    registry = SkillRegistry()
    registry.register(AddSkill())
    loop = _loop(client, registry, max_tool_rounds=2)
    outcome = await loop.run([ChatMessage(role="user", content="loop")], await _context())

    assert outcome.hit_tool_limit
    assert outcome.content == "Final answer."
    assert outcome.rounds == 2


async def test_usage_is_accumulated() -> None:
    from tests.conftest import FakeLLMClient

    client = FakeLLMClient()
    client.queue_tool("add", {"a": 1, "b": 2})
    client.queue_text("done")

    registry = SkillRegistry()
    registry.register(AddSkill())
    loop = _loop(client, registry, max_tool_rounds=5)
    outcome = await loop.run([ChatMessage(role="user", content="go")], await _context())
    assert outcome.usage.total > 0
