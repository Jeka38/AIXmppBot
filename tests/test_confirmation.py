"""Confirmation flow: pending store, reply classification and runtime (SPEC §51)."""

from __future__ import annotations

from pathlib import Path

from agent.memory.store import DataLayout, FilesystemSessionStore
from agent.runtime.runtime import AgentRuntime
from agent.security.confirmation import PendingActionStore, classify_reply
from agent.skills.base import BaseSkill, SkillResult
from agent.skills.registry import ConfirmationPolicy, SkillRegistry
from tests.conftest import FakeLLMClient, make_config
from tests.test_runtime import FakeRouter

# -- reply classification ----------------------------------------------------


def test_classify_reply() -> None:
    for yes in ("yes", "Yes", " y ", "да", "OK", "confirm", "approve"):
        assert classify_reply(yes) == "yes"
    for no in ("no", "n", "нет", "cancel", "отмена"):
        assert classify_reply(no) == "no"
    assert classify_reply("what do you mean?") is None


# -- pending store -----------------------------------------------------------


def test_pending_store_set_and_pop() -> None:
    store = PendingActionStore(ttl_seconds=60)
    store.set("s", jid="a@x", skill="del", arguments={"id": 1}, prompt="Sure?")
    action = store.pop("s")
    assert action is not None and action.skill == "del"
    assert store.peek("s") is None


def test_pending_store_expires() -> None:
    store = PendingActionStore(ttl_seconds=0)
    store.set("s", jid="a@x", skill="del", arguments={}, prompt="Sure?")
    assert store.peek("s") is None


# -- sealed skill used by the runtime test -----------------------------------


class DeleteThingSkill(BaseSkill):
    skill_name = "delete_thing"
    skill_description = "Deletes something (destructive)."
    schema = {
        "type": "object",
        "properties": {"id": {"type": "integer"}},
        "required": ["id"],
        "additionalProperties": False,
    }

    def __init__(self) -> None:
        self.executed: list[dict] = []

    async def execute(self, arguments, context):
        self.executed.append(arguments)
        return SkillResult.success(f"Deleted {arguments['id']}")


def _runtime(tmp_path: Path, skill: BaseSkill, confirmations: ConfirmationPolicy):
    config = make_config()
    client = FakeLLMClient()
    layout = DataLayout(tmp_path / "data")
    store = FilesystemSessionStore(layout.memory_dir)
    registry = SkillRegistry(confirmations=confirmations)
    registry.register(skill)
    runtime = AgentRuntime(
        config,
        FakeRouter(client),  # type: ignore[arg-type]
        registry,
        store,
        layout,
    )
    return runtime, client, registry


async def test_confirmation_blocks_until_yes(tmp_path: Path) -> None:
    skill = DeleteThingSkill()
    policy = ConfirmationPolicy(required={"delete_thing": True})
    runtime, client, _ = _runtime(tmp_path, skill, policy)

    # The model asks to run the destructive tool.
    client.queue_tool("delete_thing", {"id": 123})
    reply = await runtime.handle_dm("alice@example.com", "delete thing 123")

    assert reply is not None and "Confirm?" in reply
    assert skill.executed == []  # not executed yet

    # Confirm.
    reply2 = await runtime.handle_dm("alice@example.com", "yes")
    assert reply2 is not None and "Deleted 123" in reply2
    assert skill.executed == [{"id": 123}]


async def test_confirmation_cancelled(tmp_path: Path) -> None:
    skill = DeleteThingSkill()
    policy = ConfirmationPolicy(required={"delete_thing": True})
    runtime, client, _ = _runtime(tmp_path, skill, policy)

    client.queue_tool("delete_thing", {"id": 1})
    await runtime.handle_dm("alice@example.com", "delete it")

    reply = await runtime.handle_dm("alice@example.com", "no")
    assert reply == "Cancelled."
    assert skill.executed == []


async def test_confirmation_expires(tmp_path: Path) -> None:
    skill = DeleteThingSkill()
    policy = ConfirmationPolicy(required={"delete_thing": True})
    runtime, client, _ = _runtime(tmp_path, skill, policy)

    client.queue_tool("delete_thing", {"id": 1})
    await runtime.handle_dm("alice@example.com", "delete it")
    scope = runtime.dm_scope("alice@example.com")
    assert runtime.pending.peek(scope) is not None

    # Expire the pending action, then answer "yes": it must not execute.
    action = runtime.pending._pending[scope]  # noqa: SLF001 - test introspection
    action.ttl_seconds = 0
    action.created_at -= 10
    assert runtime.pending.peek(scope) is None

    client.queue_text("There is nothing to confirm.")
    reply = await runtime.handle_dm("alice@example.com", "yes")
    assert skill.executed == []
    assert reply is not None


async def test_dm_pending_is_isolated_between_users(tmp_path: Path) -> None:
    skill = DeleteThingSkill()
    policy = ConfirmationPolicy(required={"delete_thing": True})
    runtime, client, _ = _runtime(tmp_path, skill, policy)

    client.queue_tool("delete_thing", {"id": 7})
    await runtime.handle_dm("alice@example.com", "delete it")

    # Bob has no pending action of his own; his "yes" is a normal message.
    assert runtime.pending.peek(runtime.dm_scope("bob@example.com")) is None
    client.queue_text("Nothing pending.")
    await runtime.handle_dm("bob@example.com", "yes")
    assert skill.executed == []


async def test_muc_only_original_sender_can_confirm(tmp_path: Path) -> None:
    from agent.config.models import RoomConfig

    skill = DeleteThingSkill()
    policy = ConfirmationPolicy(required={"delete_thing": True})
    runtime, client, _ = _runtime(tmp_path, skill, policy)
    room = RoomConfig(jid="room@conference.example.com", nick="Agent", activation="all")

    client.queue_tool("delete_thing", {"id": 7})
    await runtime.handle_muc(room, "alice@example.com", "delete it", mentioned=True)

    reply = await runtime.handle_muc(room, "bob@example.com", "yes", mentioned=True)
    assert reply is not None and "only" in reply.lower()
    assert skill.executed == []


async def test_unrelated_message_clears_pending(tmp_path: Path) -> None:
    skill = DeleteThingSkill()
    policy = ConfirmationPolicy(required={"delete_thing": True})
    runtime, client, _ = _runtime(tmp_path, skill, policy)

    client.queue_tool("delete_thing", {"id": 1})
    await runtime.handle_dm("alice@example.com", "delete it")
    assert runtime.pending.peek("users/alice@example.com") is not None

    client.queue_text("Never mind, let's talk about something else.")
    await runtime.handle_dm("alice@example.com", "actually, what is XMPP?")
    assert runtime.pending.peek("users/alice@example.com") is None
