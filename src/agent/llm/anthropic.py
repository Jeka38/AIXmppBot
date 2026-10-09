"""Anthropic Messages API client."""

from __future__ import annotations

import json
from typing import Any

import httpx

from agent.errors import LLMError
from agent.llm.base import ChatMessage, LLMResponse, ToolCall, ToolSpec, Usage

_ANTHROPIC_VERSION = "2023-06-01"


class AnthropicClient:
    """Async client for the Anthropic Messages API."""

    def __init__(
        self,
        *,
        base_url: str = "https://api.anthropic.com",
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

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        system, wire_messages = _to_anthropic(messages)
        payload: dict[str, Any] = {
            "model": model or self.model,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            "temperature": (temperature if temperature is not None else self.temperature),
            "messages": wire_messages,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.parameters or {"type": "object", "properties": {}},
                }
                for tool in tools
            ]

        headers = {
            "Content-Type": "application/json",
            "anthropic-version": _ANTHROPIC_VERSION,
        }
        if self.api_key:
            headers["x-api-key"] = self.api_key

        try:
            response = await self._client.post(
                f"{self.base_url}/v1/messages", headers=headers, json=payload
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"Anthropic request failed: {exc}") from exc

        if response.status_code >= 400:
            raise LLMError(f"Anthropic returned {response.status_code}: {response.text[:500]}")
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise LLMError("Anthropic returned a non-JSON response") from exc
        return self._parse(data)

    def _parse(self, data: dict[str, Any]) -> LLMResponse:
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in data.get("content") or []:
            block_type = block.get("type")
            if block_type == "text":
                text_parts.append(block.get("text", ""))
            elif block_type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=str(block.get("id", "")),
                        name=str(block.get("name", "")),
                        arguments=block.get("input") or {},
                    )
                )
        usage_raw = data.get("usage") or {}
        usage = Usage(
            input_tokens=int(usage_raw.get("input_tokens", 0) or 0),
            output_tokens=int(usage_raw.get("output_tokens", 0) or 0),
        )
        return LLMResponse(
            content="".join(text_parts),
            tool_calls=tool_calls,
            usage=usage,
            model=str(data.get("model", "")),
            finish_reason=str(data.get("stop_reason", "")),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _to_anthropic(
    messages: list[ChatMessage],
) -> tuple[str, list[dict[str, Any]]]:
    """Translate neutral messages into Anthropic's system + messages shape."""
    system_parts: list[str] = []
    wire: list[dict[str, Any]] = []

    for message in messages:
        if message.role == "system":
            if message.content:
                system_parts.append(message.content)
            continue

        if message.role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": message.tool_call_id or "",
                "content": message.content,
            }
            _append_block(wire, "user", block)
            continue

        content_blocks: list[dict[str, Any]] = []
        if message.images:
            for image in message.images:
                content_blocks.append(_image_block(image))
        if message.content:
            content_blocks.append({"type": "text", "text": message.content})
        for call in message.tool_calls:
            content_blocks.append(
                {
                    "type": "tool_use",
                    "id": call.id,
                    "name": call.name,
                    "input": call.arguments,
                }
            )
        if not content_blocks:
            content_blocks.append({"type": "text", "text": ""})

        role = "assistant" if message.role == "assistant" else "user"
        for block in content_blocks:
            _append_block(wire, role, block)

    return "\n\n".join(system_parts), wire


def _append_block(wire: list[dict[str, Any]], role: str, block: dict[str, Any]) -> None:
    """Append *block*, merging into the previous message when roles match."""
    if wire and wire[-1]["role"] == role:
        wire[-1]["content"].append(block)
    else:
        wire.append({"role": role, "content": [block]})


def _image_block(data_url: str) -> dict[str, Any]:
    media_type = "image/png"
    data = data_url
    if data_url.startswith("data:") and "," in data_url:
        header, data = data_url.split(",", 1)
        media_type = header[5:].split(";", 1)[0] or media_type
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }
