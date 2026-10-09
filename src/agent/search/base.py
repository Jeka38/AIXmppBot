"""Search provider abstraction (SPEC §20)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str = ""
    score: float | None = None


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult] = Field(default_factory=list)
    answer: str = ""

    def as_context(self) -> str:
        """Render results as structured, citation-friendly text for the LLM."""
        lines: list[str] = [f"Search query: {self.query}"]
        if self.answer:
            lines.append(f"Direct answer: {self.answer}")
        if not self.results:
            lines.append("No results.")
            return "\n".join(lines)
        lines.append("Results:")
        for index, result in enumerate(self.results, start=1):
            lines.append(f"{index}. {result.title}\n   URL: {result.url}\n   {result.snippet}")
        return "\n".join(lines)


@runtime_checkable
class SearchProvider(Protocol):
    """A web search backend."""

    async def search(self, query: str, *, max_results: int = 5) -> SearchResponse: ...

    async def aclose(self) -> None: ...
