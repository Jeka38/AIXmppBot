"""The agentic loop: LLM → tool → result → LLM until a final answer.

This is the core of the runtime (SPEC §8). It never lets a skill error kill the
session: errors are returned to the model as tool results. Tool rounds are
bounded by ``max_tool_rounds``; on reaching the limit the loop stops calling
tools and asks the model for a final answer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from agent.errors import LLMError
from agent.llm.base import ChatMessage, ToolCall, Usage
from agent.llm.router import LLMRouter
from agent.skills.base import SkillContext
from agent.skills.registry import SkillRegistry

log = logging.getLogger(__name__)

_LIMIT_NOTE = (
    "Tool-call limit reached. Do not request more tools. "
    "Provide your best final answer using the information gathered so far, "
    "and briefly note any remaining uncertainty."
)


@dataclass
class ToolExecution:
    call: ToolCall
    ok: bool
    content: str
    confirmation_required: bool = False
    confirmation_prompt: str = ""


@dataclass
class LoopOutcome:
    content: str
    usage: Usage = field(default_factory=Usage)
    rounds: int = 0
    tool_executions: list[ToolExecution] = field(default_factory=list)
    hit_tool_limit: bool = False
    #: Assistant/tool messages generated during the loop, for persistence.
    transcript: list[ChatMessage] = field(default_factory=list)
    #: Set when a tool needs the user to confirm before it can run.
    confirmation: ToolExecution | None = None


class AgentLoop:
    """Runs one turn of the agentic conversation."""

    def __init__(
        self,
        router: LLMRouter,
        skills: SkillRegistry,
        *,
        max_tool_rounds: int = 10,
        tier: str | None = "standard",
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        allow_tools: bool = True,
    ) -> None:
        self.router = router
        self.skills = skills
        self.max_tool_rounds = max(0, max_tool_rounds)
        self.tier = tier
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.allow_tools = allow_tools

    async def run(
        self,
        messages: list[ChatMessage],
        context: SkillContext,
        *,
        allowed_skills: set[str] | None = None,
        confirmations_required: set[str] | None = None,
    ) -> LoopOutcome:
        tools = self.skills.tool_definitions(only=allowed_skills) if self.allow_tools else []
        working = list(messages)
        outcome = LoopOutcome(content="")
        confirmed = confirmations_required or set()

        for round_index in range(self.max_tool_rounds + 1):
            response = await self.router.chat(
                working,
                tools=tools or None,
                tier=self.tier,
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )
            outcome.usage.input_tokens += response.usage.input_tokens
            outcome.usage.output_tokens += response.usage.output_tokens

            if not response.tool_calls:
                outcome.content = response.content
                outcome.rounds = round_index
                return outcome

            if round_index >= self.max_tool_rounds:
                outcome.hit_tool_limit = True
                break

            # Record the assistant's tool-call turn and execute each call.
            assistant = ChatMessage(
                role="assistant",
                content=response.content,
                tool_calls=response.tool_calls,
            )
            working.append(assistant)
            outcome.transcript.append(assistant)

            for call in response.tool_calls:
                execution = await self._execute_tool(call, context, confirmed=confirmed)
                outcome.tool_executions.append(execution)
                if execution.confirmation_required:
                    # Stop the loop and let the runtime ask the user. The
                    # pending action is recorded by the caller.
                    outcome.confirmation = execution
                    outcome.rounds = round_index
                    return outcome
                tool_message = ChatMessage(
                    role="tool",
                    content=execution.content,
                    tool_call_id=call.id,
                    name=call.name,
                )
                working.append(tool_message)
                outcome.transcript.append(tool_message)
            outcome.rounds = round_index + 1

        # Tool limit reached: request a final answer without tools.
        final = await self._final_answer(working, outcome)
        return final

    async def _execute_tool(
        self,
        call: ToolCall,
        context: SkillContext,
        *,
        confirmed: set[str] | None = None,
    ) -> ToolExecution:
        result = await self.skills.execute(
            call.name,
            call.arguments,
            context,
            confirmations_required=confirmed,
        )
        if result.confirmation_required:
            return ToolExecution(
                call=call,
                ok=False,
                content="(awaiting user confirmation)",
                confirmation_required=True,
                confirmation_prompt=result.confirmation_prompt,
            )
        if result.ok:
            content = result.content or "(no output)"
        else:
            content = f"ERROR: {result.error or 'skill failed'}"
        return ToolExecution(call=call, ok=result.ok, content=content)

    async def _final_answer(self, working: list[ChatMessage], outcome: LoopOutcome) -> LoopOutcome:
        working.append(ChatMessage(role="system", content=_LIMIT_NOTE))
        try:
            response = await self.router.chat(
                working,
                tools=None,
                tier=self.tier,
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )
        except LLMError as exc:
            outcome.content = (
                f"I reached the tool-call limit and could not produce a final answer: {exc}"
            )
            return outcome
        outcome.usage.input_tokens += response.usage.input_tokens
        outcome.usage.output_tokens += response.usage.output_tokens
        outcome.content = response.content
        return outcome
