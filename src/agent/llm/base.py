"""Provider-agnostic LLM interface.

The runtime never talks to a concrete provider; it depends on the
:class:`LLMClient` protocol and the shared dataclasses defined here.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant", "tool"]


class ToolSpec(BaseModel):
    """A callable tool advertised to the model (JSON Schema parameters)."""

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class ToolCall(BaseModel):
    """A model-requested tool invocation."""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ChatMessage(BaseModel):
    """A single message in a conversation."""

    role: Role
    content: str = ""

    # Assistant tool-call requests.
    tool_calls: list[ToolCall] = Field(default_factory=list)

    # Tool result linkage.
    tool_call_id: str | None = None
    name: str | None = None

    # Optional images as data URLs (``data:image/png;base64,...``) for vision.
    images: list[str] = Field(default_factory=list)

    def to_provider(self) -> dict[str, Any]:
        """Convert to the OpenAI chat-completions wire shape."""
        message: dict[str, Any] = {"role": self.role}

        if self.images:
            parts: list[dict[str, Any]] = []
            if self.content:
                parts.append({"type": "text", "text": self.content})
            for image in self.images:
                parts.append({"type": "image_url", "image_url": {"url": image}})
            message["content"] = parts
        else:
            message["content"] = self.content

        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": _dumps(call.arguments),
                    },
                }
                for call in self.tool_calls
            ]
        if self.tool_call_id is not None:
            message["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            message["name"] = self.name
        return message


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


class LLMResponse(BaseModel):
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    model: str = ""
    finish_reason: str = ""


@runtime_checkable
class LLMClient(Protocol):
    """Minimal async chat interface every provider implements."""

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        """Send messages and return the model response (tool calls optional)."""
        ...

    async def aclose(self) -> None:
        """Release provider resources (HTTP connections)."""
        ...


def _dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)
