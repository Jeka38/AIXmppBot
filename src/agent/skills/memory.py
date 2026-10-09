"""Built-in memory skills: ``memory_store`` and ``memory_recall`` (SPEC §22).

Memory is per-caller: each skill derives its storage key from ``context.jid``
and can only ever touch that caller's own ``memory.md``. This is a deliberate
data-isolation guarantee (SPEC §57) — a skill invoked by Alice cannot read or
write Bob's memory even if the LLM asks it to.

Secrets are never stored: content that looks like a credential is rejected.
"""

from __future__ import annotations

import re
from typing import Any

from agent.memory.store import FilesystemMemoryStore, jid_to_dirname
from agent.skills.base import BaseSkill, SkillContext, SkillResult

_SECRET_PATTERNS = [
    re.compile(r"\b(sk|pk|ghp|gho|ghu|ghs|xox[baprs])[_-][A-Za-z0-9]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b[A-Za-z0-9_]{20,}\.[A-Za-z0-9_]{20,}\.[A-Za-z0-9_-]{20,}\b"),  # JWT
    re.compile(r"(?i)\b(api[_-]?key|password|secret|token)\b\s*[:=]\s*\S+"),
]


def looks_like_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in _SECRET_PATTERNS)


def user_memory_key(jid: str) -> str:
    return f"users/{jid_to_dirname(jid)}/memory.md"


class MemoryStoreSkill(BaseSkill):
    skill_name = "memory_store"
    skill_description = (
        "Store a durable fact or preference about the current user in their "
        "long-term memory. Use for stable preferences, ongoing projects and "
        "explicit requests to remember something. Never store secrets."
    )
    schema = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The fact to remember, written as a short sentence.",
            },
            "category": {
                "type": "string",
                "description": "Optional grouping label (e.g. 'preference').",
            },
        },
        "required": ["content"],
        "additionalProperties": False,
    }

    def __init__(self, memory: FilesystemMemoryStore) -> None:
        self.memory = memory

    async def execute(self, arguments: dict[str, Any], context: SkillContext) -> SkillResult:
        from agent.i18n import get_translator

        tr = get_translator()
        content = str(arguments["content"]).strip()
        if not content:
            return SkillResult.failure(tr.t("memory.nothing"))
        if looks_like_secret(content):
            return SkillResult.failure(tr.t("memory.secret_refused"))
        category = str(arguments.get("category", "")).strip()
        line = f"- {content}" + (f"  _(category: {category})_" if category else "")
        self.memory.append(user_memory_key(context.jid), line)
        return SkillResult.success(tr.t("memory.stored"), stored=content, category=category or None)


class MemoryRecallSkill(BaseSkill):
    skill_name = "memory_recall"
    skill_description = (
        "Recall everything previously stored in the current user's long-term "
        "memory. Use when the user asks what you remember about them."
    )
    schema = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    def __init__(self, memory: FilesystemMemoryStore) -> None:
        self.memory = memory

    async def execute(self, arguments: dict[str, Any], context: SkillContext) -> SkillResult:
        from agent.i18n import get_translator

        stored = self.memory.read(user_memory_key(context.jid)).strip()
        if not stored:
            return SkillResult.success(get_translator().t("memory.empty"))
        return SkillResult.success(stored, memory=stored)
