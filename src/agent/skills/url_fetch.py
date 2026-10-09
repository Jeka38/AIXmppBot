"""``url_fetch`` skill (SPEC §21).

Downloads a URL through :class:`SafeFetcher` (SSRF-guarded, size-limited,
redirect-checked), extracts readable text, and fences it as untrusted external
content so it can never act as instructions.
"""

from __future__ import annotations

from typing import Any

from agent.errors import AgentError
from agent.net.extract import decode_bytes, html_to_text, pretty_json
from agent.net.fetch import SafeFetcher
from agent.security.injection import wrap_untrusted
from agent.skills.base import BaseSkill, SkillContext, SkillResult

_MAX_OUTPUT_CHARS = 12000


class UrlFetchSkill(BaseSkill):
    skill_name = "url_fetch"
    skill_description = (
        "Fetch a public web page or document by URL and return its readable text. "
        "Use when the user gives a link or you need the content behind one of the "
        "URLs returned by web_search. Returns plain text, not raw HTML."
    )
    schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Absolute http(s) URL to fetch.",
            },
            "max_chars": {
                "type": "integer",
                "minimum": 500,
                "maximum": 60000,
                "description": "Maximum characters of extracted text to return.",
            },
        },
        "required": ["url"],
        "additionalProperties": False,
    }
    required_capabilities = ["network"]

    def __init__(self, fetcher: SafeFetcher) -> None:
        self.fetcher = fetcher

    async def execute(self, arguments: dict[str, Any], context: SkillContext) -> SkillResult:
        url = str(arguments["url"]).strip()
        if not url:
            return SkillResult.failure("No URL provided.")
        max_chars = int(arguments.get("max_chars", _MAX_OUTPUT_CHARS))
        max_chars = max(500, min(60000, max_chars))

        try:
            context.ensure_network(url)
        except AgentError as exc:
            return SkillResult.failure(f"URL fetch denied: {exc}")

        try:
            result = await self.fetcher.fetch(url)
        except AgentError as exc:
            return SkillResult.failure(f"URL fetch rejected or failed: {exc}")

        if result.status_code >= 400:
            return SkillResult.failure(f"Fetching {url} returned HTTP {result.status_code}.")

        if result.is_text:
            raw = decode_bytes(result.body, result.content_type)
            if result.content_type.endswith("json"):
                text = pretty_json(raw)
            elif "html" in result.content_type or "xml" in result.content_type:
                text = html_to_text(raw)
            else:
                text = raw
        else:
            return SkillResult.failure(
                f"Unsupported content type {result.content_type or 'unknown'!r} "
                "for url_fetch; binary documents are handled as attachments."
            )

        text = text.strip()
        if not text:
            return SkillResult.failure(f"No readable text extracted from {url}.")

        truncated = result.truncated or len(text) > max_chars
        if len(text) > max_chars:
            text = text[:max_chars] + "\n… [truncated]"
        banner = f"Fetched: {result.url} (HTTP {result.status_code}, {result.content_type})"
        if truncated:
            banner += " [truncated]"
        fenced = wrap_untrusted(f"{banner}\n\n{text}", source=result.url)
        return SkillResult.success(
            fenced,
            url=result.url,
            status_code=result.status_code,
            content_type=result.content_type,
        )
