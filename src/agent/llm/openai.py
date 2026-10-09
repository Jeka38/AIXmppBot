"""OpenAI-compatible chat client.

Works with OpenAI, OpenRouter, RouterAI, Ollama's ``/v1`` endpoint and any
other server exposing the ``/chat/completions`` API.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from agent.errors import LLMError
from agent.llm.base import ChatMessage, LLMResponse, ToolCall, ToolSpec, Usage


class OpenAIClient:
    """Async client for OpenAI-compatible chat completion APIs."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str = "",
        model: str = "",
        max_tokens: int = 8192,
        temperature: float = 0.7,
        timeout: float = 120.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": [message.to_provider() for message in messages],
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            "temperature": (temperature if temperature is not None else self.temperature),
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters or {"type": "object", "properties": {}},
                    },
                }
                for tool in tools
            ]

        try:
            response = await self._client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc

        if response.status_code >= 400:
            raise LLMError(f"LLM provider returned {response.status_code}: {response.text[:500]}")

        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise LLMError("LLM provider returned a non-JSON response") from exc
        return self._parse(data)

    def _parse(self, data: dict[str, Any]) -> LLMResponse:
        choices = data.get("choices") or []
        if not choices:
            raise LLMError("LLM response contained no choices")
        choice = choices[0]
        message = choice.get("message") or {}

        tool_calls: list[ToolCall] = []
        for raw in message.get("tool_calls") or []:
            function = raw.get("function") or {}
            tool_calls.append(
                ToolCall(
                    id=str(raw.get("id", "")),
                    name=str(function.get("name", "")),
                    arguments=_loads_arguments(function.get("arguments")),
                )
            )

        usage_raw = data.get("usage") or {}
        usage = Usage(
            input_tokens=int(usage_raw.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage_raw.get("completion_tokens", 0) or 0),
        )
        return LLMResponse(
            content=message.get("content") or "",
            tool_calls=tool_calls,
            usage=usage,
            model=str(data.get("model", "")),
            finish_reason=str(choice.get("finish_reason", "")),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _loads_arguments(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return {}
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return {"_raw": stripped}
        return parsed if isinstance(parsed, dict) else {"_value": parsed}
    return {}
