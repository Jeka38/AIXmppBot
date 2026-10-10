"""End-to-end runtime flows: authorization, DM handling, commands, MUC."""

from __future__ import annotations

from agent.config.models import RoomConfig
from agent.memory.store import DataLayout, FilesystemSessionStore
from agent.runtime.runtime import AgentRuntime
from agent.security.credentials import Redactor
from agent.skills.registry import SkillRegistry
from tests.conftest import FakeLLMClient, make_config


class FakeRouter:
    def __init__(self, client: FakeLLMClient) -> None:
        self.client = client

    async def chat(self, messages, **kwargs):
        return await self.client.chat(messages, tools=kwargs.get("tools"))

    async def aclose(self) -> None:
        await self.client.aclose()


def _runtime(tmp_path, responses=None, **config_overrides):
    config = make_config(**config_overrides)
    client = FakeLLMClient(responses)
    layout = DataLayout(tmp_path / "data")
    store = FilesystemSessionStore(layout.memory_dir)
    registry = SkillRegistry()
    runtime = AgentRuntime(
        config,
        FakeRouter(client),  # type: ignore[arg-type]
        registry,
        store,
        layout,
        redactor=Redactor(),
    )
    return runtime, client, layout, store


async def test_unauthorized_dm_ignored(tmp_path) -> None:
    runtime, client, _, _ = _runtime(tmp_path)
    reply = await runtime.handle_dm("mallory@evil.com", "hello")
    assert reply is None
    assert client.calls == []


async def test_authorized_dm_answered(tmp_path) -> None:
    runtime, client, _, _ = _runtime(tmp_path)
    client.queue_text("Hello Alice!")
    reply = await runtime.handle_dm("alice@example.com", "hi")
    assert reply == "Hello Alice!"
    # System prompt + user message were sent.
    assert client.calls[0][0].role == "system"
    assert client.calls[0][-1].content == "hi"


async def test_dm_history_persisted(tmp_path) -> None:
    runtime, client, layout, store = _runtime(tmp_path)
    client.queue_text("first reply")
    await runtime.handle_dm("alice@example.com", "first message")

    reloaded, skipped = store.load_lenient(layout.scope_for_user("alice@example.com"))
    assert skipped == 0
    assert [e.content for e in reloaded] == ["first message", "first reply"]


async def test_command_bypasses_llm(tmp_path) -> None:
    runtime, client, _, _ = _runtime(tmp_path)
    reply = await runtime.handle_dm("alice@example.com", "/ping")
    assert reply == "pong"
    assert client.calls == []  # command never reached the model


async def test_forget_clears_memory(tmp_path) -> None:
    runtime, client, layout, _ = _runtime(tmp_path)
    # Store something, then forget it.
    await runtime.handle_dm("alice@example.com", "/help")
    key = f"users/{layout.user_dir('alice@example.com').name}/memory.md"
    runtime.memory.write(key, "- remembered fact")
    assert runtime.memory.read(key) == "- remembered fact"

    client.queue_text("x")
    reply = await runtime.handle_dm("alice@example.com", "/forget")
    assert reply is not None
    assert runtime.memory.read(key) == ""


async def test_muc_mention_mode_ignores_unmentioned(tmp_path) -> None:
    runtime, client, _, _ = _runtime(tmp_path)
    room = RoomConfig(jid="room@conference.example.com", nick="Agent", activation="mention")
    reply = await runtime.handle_muc(room, "alice@example.com", "just chatting", mentioned=False)
    assert reply is None
    assert client.calls == []


async def test_muc_mention_mode_answers_when_mentioned(tmp_path) -> None:
    runtime, client, _, _ = _runtime(tmp_path)
    client.queue_text("Sure, here is the info.")
    room = RoomConfig(jid="room@conference.example.com", nick="Agent", activation="mention")
    reply = await runtime.handle_muc(room, "alice@example.com", "@Agent help", mentioned=True)
    assert reply == "Sure, here is the info."


async def test_muc_all_mode_answers_everything(tmp_path) -> None:
    runtime, client, _, _ = _runtime(tmp_path)
    client.queue_text("ack")
    room = RoomConfig(jid="room@conference.example.com", nick="Agent", activation="all")
    reply = await runtime.handle_muc(room, "alice@example.com", "random message", mentioned=False)
    assert reply == "ack"


async def test_muc_restricted_room_ignores_outsider(tmp_path) -> None:
    runtime, client, _, _ = _runtime(tmp_path)
    room = RoomConfig(
        jid="room@conference.example.com",
        nick="Agent",
        activation="all",
        restrict_to_allowed_jids=True,
    )
    reply = await runtime.handle_muc(room, "mallory@evil.com", "do something", mentioned=True)
    assert reply is None
    assert client.calls == []


async def test_muc_public_room_answers_anonymous_occupant(tmp_path) -> None:
    runtime, client, _, _ = _runtime(tmp_path)
    client.queue_text("hello room")
    room = RoomConfig(jid="room@conference.example.com", nick="Agent", activation="all")
    reply = await runtime.handle_muc(
        room,
        "room@conference.example.com/Bob",
        "hi everyone",
        mentioned=False,
        anonymous=True,
    )
    assert reply == "hello room"


async def test_muc_room_allowlist_enforced(tmp_path) -> None:
    runtime, client, _, _ = _runtime(tmp_path)
    room = RoomConfig(
        jid="room@conference.example.com",
        nick="Agent",
        activation="all",
        allowed_jids=["bob@example.com"],
    )
    # Alice is globally authorized but excluded from this room.
    assert await runtime.handle_muc(room, "alice@example.com", "hi", mentioned=True) is None
    client.queue_text("bob reply")
    assert await runtime.handle_muc(room, "bob@example.com", "hi", mentioned=True) == "bob reply"


async def test_llm_error_does_not_crash(tmp_path) -> None:
    from agent.errors import LLMError

    runtime, client, _, _ = _runtime(tmp_path)

    async def boom(*args, **kwargs):
        raise LLMError("provider down")

    client.chat = boom  # type: ignore[assignment]
    reply = await runtime.handle_dm("alice@example.com", "hi")
    assert reply is not None
    assert "error" in reply.lower()


async def test_join_command_uses_room_controller(tmp_path) -> None:
    from agent.config.models import RoomConfig as _RoomConfig

    class Controller:
        def __init__(self) -> None:
            self.joined: list[_RoomConfig] = []
            self.left: list[str] = []

        async def join_room(self, room: _RoomConfig) -> None:
            self.joined.append(room)

        async def leave_room(self, room_jid: str) -> None:
            self.left.append(room_jid)

        def list_rooms(self) -> list[_RoomConfig]:
            return list(self.joined)

    config = make_config()
    layout = DataLayout(tmp_path / "data")
    store = FilesystemSessionStore(layout.memory_dir)
    controller = Controller()
    runtime = AgentRuntime(
        config,
        FakeRouter(FakeLLMClient()),  # type: ignore[arg-type]
        SkillRegistry(),
        store,
        layout,
        redactor=Redactor(),
        rooms=controller,  # type: ignore[arg-type]
    )
    # admin@example.com is a dev_command_jid in make_config().
    reply = await runtime.handle_dm("admin@example.com", "/join room@conference.example.com")
    assert reply is not None
    assert len(controller.joined) == 1
    assert controller.joined[0].jid == "room@conference.example.com"

    reply = await runtime.handle_dm("admin@example.com", "/leave room@conference.example.com")
    assert reply is not None
    assert controller.left == ["room@conference.example.com"]


async def test_join_command_denied_for_non_admin(tmp_path) -> None:
    runtime, client, _, _ = _runtime(tmp_path)
    reply = await runtime.handle_dm("alice@example.com", "/join room@conference.example.com")
    assert reply is not None
    assert "administrators only" in reply


async def test_room_owner_can_change_mode(tmp_path) -> None:
    class Controller:
        def __init__(self) -> None:
            self.modes: list[tuple[str, str]] = []

        async def join_room(self, room):  # pragma: no cover
            pass

        async def leave_room(self, room_jid):  # pragma: no cover
            pass

        def set_room_mode(self, room_jid: str, mode: str) -> None:
            self.modes.append((room_jid, mode))

        def list_rooms(self):
            return []

    config = make_config()
    layout = DataLayout(tmp_path / "data")
    store = FilesystemSessionStore(layout.memory_dir)
    controller = Controller()
    runtime = AgentRuntime(
        config,
        FakeRouter(FakeLLMClient()),  # type: ignore[arg-type]
        SkillRegistry(),
        store,
        layout,
        redactor=Redactor(),
        rooms=controller,  # type: ignore[arg-type]
    )
    room = RoomConfig(jid="room@conference.example.com", nick="Bot", activation="mention")
    # Owner: command runs even without a mention.
    reply = await runtime.handle_muc(
        room,
        "room@conference.example.com/Alice",
        "/mode all",
        mentioned=False,
        anonymous=True,
        affiliation="owner",
    )
    assert reply is not None and controller.modes == [("room@conference.example.com", "all")]

    # Member: denied, no mode change.
    reply = await runtime.handle_muc(
        room,
        "room@conference.example.com/Bob",
        "/mode all",
        mentioned=False,
        anonymous=True,
        affiliation="member",
    )
    assert reply is None or "owner" in reply.lower()
    assert len(controller.modes) == 1


async def test_persona_command_works_in_room(tmp_path) -> None:

    from agent.config.models import PersonaConfig
    from agent.personas.manager import PersonaManager

    config = make_config()
    layout = DataLayout(tmp_path / "data")
    store = FilesystemSessionStore(layout.memory_dir)
    personas_dir = tmp_path / "personas"
    for name in ("general-assistant", "coder"):
        d = personas_dir / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "persona.toml").write_text(
            f'[persona]\nid = "{name}"\nname = "{name}"\n', encoding="utf-8"
        )
    manager = PersonaManager(
        layout,
        PersonaConfig(default_persona="general-assistant"),
        global_dirs=[personas_dir],
    )
    runtime = AgentRuntime(
        config,
        FakeRouter(FakeLLMClient()),  # type: ignore[arg-type]
        SkillRegistry(),
        store,
        layout,
        redactor=Redactor(),
        personas=manager,
    )
    room = RoomConfig(jid="room@conference.example.com", nick="Bot", activation="mention")

    # An ordinary occupant (member) can use /persona from the room.
    reply = await runtime.handle_muc(
        room,
        "room@conference.example.com/Bob",
        "/persona list",
        mentioned=False,
        anonymous=True,
        affiliation="member",
    )
    assert reply is not None
    assert "general-assistant" in reply

    reply = await runtime.handle_muc(
        room,
        "room@conference.example.com/Bob",
        "/persona coder",
        mentioned=False,
        anonymous=True,
        affiliation="member",
    )
    assert reply is not None
    assert manager.active_room_persona(room.jid) == "coder"
    # The user's own (DM) persona is unaffected.
    assert manager.active_persona("bob@example.com") == "general-assistant"


def test_persona_create_always_allowed_skill() -> None:
    from agent.runtime.runtime import AgentRuntime

    # An empty persona skill list must not hide persona_create.
    assert "persona_create" in AgentRuntime._ALWAYS_ALLOWED_SKILLS


async def test_persona_create_offered_even_with_restricted_persona(tmp_path) -> None:
    from agent.personas.manager import PersonaManager

    config = make_config()
    layout = DataLayout(tmp_path / "data")
    store = FilesystemSessionStore(layout.memory_dir)
    personas_dir = tmp_path / "personas"
    d = personas_dir / "picky"
    d.mkdir(parents=True, exist_ok=True)
    (d / "persona.toml").write_text(
        '[persona]\nid = "picky"\nname = "picky"\n'
        '[persona.defaults]\ntier = "standard"\nskills = ["web_search"]\n',
        encoding="utf-8",
    )
    manager = PersonaManager(
        layout,
        __import__("agent.config.models", fromlist=["PersonaConfig"]).PersonaConfig(
            default_persona="picky"
        ),
        global_dirs=[personas_dir],
    )
    runtime = AgentRuntime(
        config,
        FakeRouter(FakeLLMClient()),  # type: ignore[arg-type]
        SkillRegistry(),
        store,
        layout,
        redactor=Redactor(),
        personas=manager,
    )
    allowed = runtime._allowed_skills(None, {"web_search"})  # noqa: SLF001
    assert allowed is not None and "persona_create" in allowed


async def test_room_owner_ignores_nick_via_command(tmp_path) -> None:
    from agent.security.ignore_store import IgnoreStore

    config = make_config()
    layout = DataLayout(tmp_path / "data")
    store = FilesystemSessionStore(layout.memory_dir)
    ignore = IgnoreStore(tmp_path / "ignored.json")
    runtime = AgentRuntime(
        config,
        FakeRouter(FakeLLMClient()),  # type: ignore[arg-type]
        SkillRegistry(),
        store,
        layout,
        redactor=Redactor(),
        ignore_store=ignore,
    )
    room = RoomConfig(jid="room@conference.example.com", nick="Bot", activation="all")

    # Owner bans a nick in this room.
    reply = await runtime.handle_muc(
        room,
        "room@conference.example.com/Owner",
        "/ignore nick:Flooder",
        mentioned=False,
        affiliation="owner",
        nick="Owner",
    )
    assert reply is not None and ignore.room_nicks(room.jid) == ["flooder"]

    # The banned nick is now silent in this room.
    assert (
        await runtime.handle_muc(
            room,
            "room@conference.example.com/Flooder",
            "hello",
            mentioned=True,
            affiliation="member",
            nick="Flooder",
        )
        is None
    )

    # A different nick still gets a reply.
    client = FakeLLMClient()
    client.queue_text("hi there")
    runtime.router = FakeRouter(client)  # type: ignore[assignment]
    reply = await runtime.handle_muc(
        room,
        "room@conference.example.com/Alice",
        "hello",
        mentioned=True,
        affiliation="member",
        nick="Alice",
    )
    assert reply == "hi there"
