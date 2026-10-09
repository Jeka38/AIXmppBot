"""Ollama chat client (native ``/api/chat`` with tool support).

No API key required. Falls back gracefully when the model does not support
tool calling: tools are simply ignored by the server.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from agent.errors import LLMError
from agent.llm.base import ChatMessage, LLMResponse, ToolCall, ToolSpec, Usage


class OllamaClient:
    """Async client for a local Ollama server."""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:11434",
        model: str = "",
        max_tokens: int = 8192,
        temperature: float = 0.7,
        timeout: float = 120.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    def _to_ollama(self, message: ChatMessage) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "role": message.role,
            "content": message.content,
        }
        if message.images:
            payload["images"] = [_strip_data_url(image) for image in message.images]
        if message.tool_calls:
            payload["tool_calls"] = [
                {"function": {"name": call.name, "arguments": call.arguments}}
                for call in message.tool_calls
            ]
        if message.role == "tool":
            payload["role"] = "tool"
        return payload

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
            "messages": [self._to_ollama(message) for message in messages],
            "stream": False,
            "options": {
                "num_predict": max_tokens if max_tokens is not None else self.max_tokens,
                "temperature": (temperature if temperature is not None else self.temperature),
            },
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
            response = await self._client.post(f"{self.base_url}/api/chat", json=payload)
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama request failed: {exc}") from exc

        if response.status_code >= 400:
            raise LLMError(f"Ollama returned {response.status_code}: {response.text[:500]}")
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise LLMError("Ollama returned a non-JSON response") from exc
        return self._parse(data)

    def _parse(self, data: dict[str, Any]) -> LLMResponse:
        message = data.get("message") or {}
        tool_calls: list[ToolCall] = []
        for index, raw in enumerate(message.get("tool_calls") or []):
            function = raw.get("function") or {}
            tool_calls.append(
                ToolCall(
                    id=str(raw.get("id", f"call_{index}")),
                    name=str(function.get("name", "")),
                    arguments=function.get("arguments") or {},
                )
            )

        usage = Usage(
            input_tokens=int(data.get("prompt_eval_count", 0) or 0),
            output_tokens=int(data.get("eval_count", 0) or 0),
        )
        return LLMResponse(
            content=message.get("content") or "",
            tool_calls=tool_calls,
            usage=usage,
            model=str(data.get("model", "")),
            finish_reason=str(data.get("done_reason", "")),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _strip_data_url(value: str) -> str:
    """Ollama expects bare base64, not a ``data:`` URL."""
    if value.startswith("data:") and "," in value:
        return value.split(",", 1)[1]
    return value
