"""LLM-driven context compaction (SPEC §24)."""

from __future__ import annotations

from agent.llm.tokenizer import TokenEstimator
from agent.memory.session import SessionEvent
from agent.memory.summarize import Compactor
from tests.conftest import FakeLLMClient


class FakeRouter:
    def __init__(self, client: FakeLLMClient) -> None:
        self.client = client

    async def chat(self, messages, **kwargs):
        return await self.client.chat(messages)

    async def aclose(self) -> None:
        await self.client.aclose()


def _events(count: int, *, size: int = 200, sender: str = "alice@example.com"):
    return [
        SessionEvent(
            type="message",
            role="user",
            sender=sender,
            content=f"message {index} " + "x" * size,
        )
        for index in range(count)
    ]


async def test_no_compaction_within_budget() -> None:
    client = FakeLLMClient()
    compactor = Compactor(FakeRouter(client), TokenEstimator("t"), keep_recent=5)  # type: ignore[arg-type]
    events = _events(3, size=10)
    new_events, result = await compactor.compact(events, budget=100000)
    assert not result.compacted
    assert new_events is events
    assert client.calls == []


async def test_compaction_summarises_oldest() -> None:
    client = FakeLLMClient()
    client.queue_text("Summary of earlier conversation.")
    compactor = Compactor(FakeRouter(client), TokenEstimator("t"), keep_recent=3)  # type: ignore[arg-type]
    events = _events(10, size=400)

    new_events, result = await compactor.compact(events, budget=100)
    assert result.compacted
    assert result.summarised == 7
    assert new_events[0].type == "summary"
    assert new_events[0].content == "Summary of earlier conversation."
    assert new_events[0].compacted_count == 7
    # The recent window is preserved verbatim.
    assert new_events[-3].content == events[-3].content


async def test_compaction_failure_leaves_history_intact() -> None:
    from agent.errors import LLMError

    client = FakeLLMClient()

    async def boom(*args, **kwargs):
        raise LLMError("down")

    client.chat = boom  # type: ignore[assignment]
    compactor = Compactor(FakeRouter(client), TokenEstimator("t"), keep_recent=3)  # type: ignore[arg-type]
    events = _events(10, size=400)

    new_events, result = await compactor.compact(events, budget=100)
    assert not result.compacted
    assert new_events == events


async def test_compaction_never_splits_tool_pair() -> None:
    client = FakeLLMClient()
    client.queue_text("summary")
    compactor = Compactor(FakeRouter(client), TokenEstimator("t"), keep_recent=2)  # type: ignore[arg-type]
    events = [
        SessionEvent(type="message", role="user", content="x" * 400),
        SessionEvent(type="tool_call", tool="web_search", arguments={}),
        SessionEvent(type="tool_result", tool="web_search", content="y" * 400),
        SessionEvent(type="message", role="assistant", content="z" * 400),
    ]
    new_events, result = await compactor.compact(events, budget=50)
    # The recent window must not begin with an orphaned tool_result.
    if result.compacted:
        assert new_events[1].type != "tool_result"


async def test_summary_prompt_mentions_authors() -> None:
    client = FakeLLMClient()
    client.queue_text("summary")
    compactor = Compactor(FakeRouter(client), TokenEstimator("t"), keep_recent=1)  # type: ignore[arg-type]
    events = [
        SessionEvent(type="message", role="user", sender="alice@example.com", content="a" * 500),
        SessionEvent(type="message", role="user", sender="bob@example.com", content="b" * 500),
        SessionEvent(type="message", role="assistant", content="c" * 500),
    ]
    await compactor.compact(events, budget=50)
    sent = "\n".join(message.content for message in client.calls[0])
    assert "alice@example.com" in sent
    assert "bob@example.com" in sent
