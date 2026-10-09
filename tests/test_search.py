"""Web search providers and the ``web_search`` skill (SPEC §20)."""

from __future__ import annotations

import httpx
import pytest

from agent.errors import SkillError
from agent.search.base import SearchResponse, SearchResult
from agent.search.tavily import TavilyProvider
from agent.security.capabilities import CapabilitySet
from agent.skills.base import SkillContext
from agent.skills.web_search import WebSearchSkill


class StubProvider:
    def __init__(self, response: SearchResponse) -> None:
        self.response = response
        self.queries: list[tuple[str, int]] = []

    async def search(self, query: str, *, max_results: int = 5) -> SearchResponse:
        self.queries.append((query, max_results))
        return self.response

    async def aclose(self) -> None:
        pass


def _context() -> SkillContext:
    return SkillContext(
        jid="alice@example.com",
        capabilities=CapabilitySet.from_mapping({"network": []}),
    )


async def test_search_skill_returns_fenced_results() -> None:
    provider = StubProvider(
        SearchResponse(
            query="xmpp",
            results=[
                SearchResult(title="XMPP", url="https://xmpp.org", snippet="Protocol"),
            ],
        )
    )
    skill = WebSearchSkill(provider)  # type: ignore[arg-type]
    result = await skill.execute({"query": "xmpp"}, _context())

    assert result.ok
    assert "xmpp.org" in result.content
    assert "UNTRUSTED_EXTERNAL_CONTENT" in result.content
    assert result.data["result_count"] == 1


async def test_search_skill_rejects_empty_query() -> None:
    provider = StubProvider(SearchResponse(query=""))
    skill = WebSearchSkill(provider)  # type: ignore[arg-type]
    result = await skill.execute({"query": "   "}, _context())
    assert not result.ok


async def test_search_skill_clamps_max_results() -> None:
    provider = StubProvider(SearchResponse(query="q"))
    skill = WebSearchSkill(provider)  # type: ignore[arg-type]
    await skill.execute({"query": "q", "max_results": 999}, _context())
    assert provider.queries[0][1] == 10


def test_tavily_requires_key() -> None:
    with pytest.raises(SkillError):
        TavilyProvider("")


async def test_tavily_parses_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "answer": "42",
                "results": [
                    {"title": "T", "url": "https://x.test", "content": "snip", "score": 0.9}
                ],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = TavilyProvider("key", client=client)
    response = await provider.search("meaning of life", max_results=3)
    assert response.answer == "42"
    assert response.results[0].url == "https://x.test"
    assert response.results[0].score == 0.9


async def test_tavily_error_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = TavilyProvider("key", client=client)
    with pytest.raises(SkillError):
        await provider.search("q")
