"""Tavily search provider."""

from __future__ import annotations

import httpx

from agent.errors import SkillError
from agent.search.base import SearchResponse, SearchResult

_ENDPOINT = "https://api.tavily.com/search"


class TavilyProvider:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = _ENDPOINT,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise SkillError("web_search provider 'tavily' requires an API key")
        self.api_key = api_key
        self.base_url = base_url
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    async def search(self, query: str, *, max_results: int = 5) -> SearchResponse:
        payload = {
            "api_key": self.api_key,
            "query": query,
            "max_results": max_results,
            "include_answer": True,
            "search_depth": "basic",
        }
        try:
            response = await self._client.post(self.base_url, json=payload)
        except httpx.HTTPError as exc:
            raise SkillError(f"Tavily request failed: {exc}") from exc
        if response.status_code >= 400:
            raise SkillError(f"Tavily returned {response.status_code}: {response.text[:300]}")

        data = response.json()
        results = [
            SearchResult(
                title=str(item.get("title", "")).strip(),
                url=str(item.get("url", "")).strip(),
                snippet=str(item.get("content", "")).strip(),
                score=_as_float(item.get("score")),
            )
            for item in data.get("results", [])
        ]
        return SearchResponse(
            query=query,
            results=results[:max_results],
            answer=str(data.get("answer") or ""),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
