"""Human confirmation for destructive skill actions (SPEC §51).

When a skill configured to require confirmation is invoked, the runtime must not
execute it. Instead it records a :class:`PendingAction` scoped to the
conversation and asks the user. Only an explicit affirmative reply from the same
scope (and the same JID in a DM) executes the action; anything else cancels it.

Actions expire after a short TTL so a stale "yes" cannot trigger an old action.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

_AFFIRMATIVE = {"yes", "y", "да", "д", "confirm", "ok", "approve", "подтверждаю"}
_NEGATIVE = {"no", "n", "нет", "н", "cancel", "отмена", "отменить", "stop"}


@dataclass
class PendingAction:
    scope: str
    jid: str
    skill: str
    arguments: dict[str, Any]
    prompt: str
    created_at: float = field(default_factory=time.monotonic)
    ttl_seconds: float = 300.0

    def is_expired(self, *, now: float | None = None) -> bool:
        current = now if now is not None else time.monotonic()
        return (current - self.created_at) > self.ttl_seconds


class PendingActionStore:
    """In-memory per-scope store of actions awaiting confirmation."""

    def __init__(self, *, ttl_seconds: float = 300.0) -> None:
        self.ttl_seconds = ttl_seconds
        self._pending: dict[str, PendingAction] = {}

    def set(
        self,
        scope: str,
        *,
        jid: str,
        skill: str,
        arguments: dict[str, Any],
        prompt: str,
    ) -> PendingAction:
        action = PendingAction(
            scope=scope,
            jid=jid,
            skill=skill,
            arguments=arguments,
            prompt=prompt,
            ttl_seconds=self.ttl_seconds,
        )
        self._pending[scope] = action
        return action

    def peek(self, scope: str) -> PendingAction | None:
        action = self._pending.get(scope)
        if action is None:
            return None
        if action.is_expired():
            del self._pending[scope]
            return None
        return action

    def pop(self, scope: str) -> PendingAction | None:
        action = self.peek(scope)
        self._pending.pop(scope, None)
        return action

    def clear(self, scope: str) -> None:
        self._pending.pop(scope, None)

    def purge_expired(self) -> int:
        expired = [scope for scope, action in self._pending.items() if action.is_expired()]
        for scope in expired:
            del self._pending[scope]
        return len(expired)


def classify_reply(text: str) -> str | None:
    """Return ``"yes"``, ``"no"`` or ``None`` for a confirmation reply."""
    token = text.strip().lower().rstrip("!.")
    if token in _AFFIRMATIVE:
        return "yes"
    if token in _NEGATIVE:
        return "no"
    return None
