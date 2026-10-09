"""Perplexity search provider (OpenAI-compatible chat completions).

Perplexity returns a synthesised answer with citations rather than a result
list; citations are mapped to :class:`SearchResult` entries.
"""

from __future__ import annotations

import httpx

from agent.errors import SkillError
from agent.search.base import SearchResponse, SearchResult

_ENDPOINT = "https://api.perplexity.ai/chat/completions"
_DEFAULT_MODEL = "sonar"


class PerplexityProvider:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = _DEFAULT_MODEL,
        base_url: str = _ENDPOINT,
        timeout: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise SkillError("web_search provider 'perplexity' requires an API key")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    async def search(self, query: str, *, max_results: int = 5) -> SearchResponse:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "Answer concisely and cite sources.",
                },
                {"role": "user", "content": query},
            ],
            "max_tokens": 1024,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = await self._client.post(self.base_url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise SkillError(f"Perplexity request failed: {exc}") from exc
        if response.status_code >= 400:
            raise SkillError(f"Perplexity returned {response.status_code}: {response.text[:300]}")

        data = response.json()
        choices = data.get("choices") or []
        answer = ""
        if choices:
            answer = str((choices[0].get("message") or {}).get("content", ""))
        citations = data.get("citations") or []
        results = [
            SearchResult(title=f"Source {index}", url=str(url), snippet="")
            for index, url in enumerate(citations[:max_results], start=1)
        ]
        return SearchResponse(query=query, results=results, answer=answer)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
