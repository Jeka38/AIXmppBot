"""``web_search`` skill (SPEC §20).

Results are returned to the LLM as structured, citation-friendly text. Search
results are external content and are fenced by the runtime's injection policy
before being trusted; here we keep them clearly labelled as data.
"""

from __future__ import annotations

from typing import Any

from agent.errors import AgentError
from agent.search.base import SearchProvider
from agent.skills.base import BaseSkill, SkillContext, SkillResult


class WebSearchSkill(BaseSkill):
    skill_name = "web_search"
    skill_description = (
        "Search the public web and return ranked results with titles, URLs and "
        "snippets. Use for current events, facts you are unsure about, or when "
        "the user asks you to look something up."
    )
    schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query.",
            },
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": "Maximum number of results to return.",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    required_capabilities = ["network"]

    def __init__(
        self,
        provider: SearchProvider,
        *,
        max_results: int = 5,
    ) -> None:
        self.provider = provider
        self.max_results = max_results

    async def execute(self, arguments: dict[str, Any], context: SkillContext) -> SkillResult:
        query = str(arguments["query"]).strip()
        if not query:
            return SkillResult.failure("Search query is empty.")
        limit = int(arguments.get("max_results", self.max_results))
        limit = max(1, min(10, limit))
        try:
            response = await self.provider.search(query, max_results=limit)
        except AgentError as exc:
            return SkillResult.failure(f"Web search failed: {exc}")

        from agent.security.injection import wrap_untrusted

        fenced = wrap_untrusted(response.as_context(), source=f"web_search:{query}")
        return SkillResult.success(
            fenced,
            query=query,
            result_count=len(response.results),
            urls=[result.url for result in response.results],
        )
