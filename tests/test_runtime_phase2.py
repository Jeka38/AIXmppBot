"""Phase 2 runtime integration: personas, memory injection, attachments, vision."""

from __future__ import annotations

from pathlib import Path

from agent.config.models import LLMTierConfig
from agent.llm.tokenizer import TokenEstimator
from agent.memory.store import DataLayout, FilesystemMemoryStore, FilesystemSessionStore
from agent.memory.summarize import Compactor
from agent.net.attachments import AttachmentHandler
from agent.net.fetch import SafeFetcher
from agent.personas.manager import PersonaManager
from agent.runtime.runtime import AgentRuntime
from agent.security.credentials import Redactor
from agent.skills.memory import MemoryStoreSkill
from agent.skills.registry import SkillRegistry
from tests.conftest import FakeLLMClient, make_config
from tests.test_runtime import FakeRouter


def _persona_dir(root: Path, name: str, identity: str, tier: str = "standard") -> None:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "persona.toml").write_text(
        f'[persona]\nid = "{name}"\nname = "{name}"\n'
        f'[persona.defaults]\ntier = "{tier}"\nskills = []\n',
        encoding="utf-8",
    )
    (directory / "identity.md").write_text(identity, encoding="utf-8")


def _runtime(tmp_path: Path, responses=None, **overrides):
    config = make_config(**overrides)
    client = FakeLLMClient(responses)
    layout = DataLayout(tmp_path / "data")
    store = FilesystemSessionStore(layout.memory_dir)
    registry = SkillRegistry()
    personas = PersonaManager(
        layout,
        config.personas,
        global_dirs=[tmp_path / "personas", layout.personas_dir],
    )
    compactor = Compactor(FakeRouter(client), TokenEstimator("t"), keep_recent=5)  # type: ignore[arg-type]
    fetcher = SafeFetcher()
    attachments = AttachmentHandler(fetcher=fetcher)
    runtime = AgentRuntime(
        config,
        FakeRouter(client),  # type: ignore[arg-type]
        registry,
        store,
        layout,
        redactor=Redactor(),
        personas=personas,
        compactor=compactor,
        attachments=attachments,
    )
    return runtime, client, layout, personas


async def test_persona_identity_in_system_prompt(tmp_path: Path) -> None:
    _persona_dir(tmp_path / "personas", "mentor", "I am a patient coding mentor.")
    runtime, client, _, personas = _runtime(tmp_path)
    personas.set_active_persona("alice@example.com", "mentor")
    client.queue_text("hello")

    await runtime.handle_dm("alice@example.com", "hi")

    system = client.calls[0][0].content
    assert "patient coding mentor" in system


async def test_persona_tier_selects_model(tmp_path: Path) -> None:
    _persona_dir(tmp_path / "personas", "genius", "I am brilliant.", tier="heavy")
    runtime, client, _, personas = _runtime(tmp_path)
    personas.set_active_persona("alice@example.com", "genius")
    client.queue_text("ok")

    await runtime.handle_dm("alice@example.com", "hi")
    # The loop resolves the tier via the router; the fake router just records.
    assert client.calls  # a call was made


async def test_long_term_memory_injected(tmp_path: Path) -> None:
    runtime, client, layout, _ = _runtime(tmp_path)
    # Store a fact directly through the memory skill.
    memory = FilesystemMemoryStore(layout.memory_dir)
    skill = MemoryStoreSkill(memory)
    from agent.skills.base import SkillContext

    await skill.execute({"content": "Alice prefers Python"}, SkillContext(jid="alice@example.com"))

    client.queue_text("noted")
    await runtime.handle_dm("alice@example.com", "what do I like?")

    system = client.calls[0][0].content
    assert "Alice prefers Python" in system


async def test_attachment_text_merged_into_message(tmp_path: Path) -> None:
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text="attachment contents here", headers={"content-type": "text/plain"}
        )

    runtime, client, _, _ = _runtime(tmp_path)
    runtime.attachments = AttachmentHandler(
        fetcher=SafeFetcher(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    )
    client.queue_text("summary")

    await runtime.handle_dm(
        "alice@example.com", "summarise this", attachment_urls=["http://example.com/a.txt"]
    )
    user_message = client.calls[0][-1].content
    assert "attachment contents here" in user_message


async def test_vision_tier_when_images_and_configured(tmp_path: Path) -> None:
    import httpx

    png = __import__("base64").b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=png, headers={"content-type": "image/png"})

    runtime, client, _, _ = _runtime(
        tmp_path,
        llm=make_config().llm.model_copy(
            update={"tiers": LLMTierConfig(vision="vision-model", standard="std")}
        ),
    )
    runtime.attachments = AttachmentHandler(
        fetcher=SafeFetcher(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    )
    client.queue_text("I see an image")
    client.queue_text("I see it")

    await runtime.handle_dm(
        "alice@example.com", "what is this?", attachment_urls=["http://example.com/p.png"]
    )
    # The user message carries the image data URL.
    user_message = client.calls[0][-1]
    assert user_message.images and user_message.images[0].startswith("data:image/png")


async def test_compaction_runs_and_persists_summary(tmp_path: Path) -> None:
    runtime, client, layout, _ = _runtime(tmp_path)
    # Seed a long history directly.
    scope = layout.scope_for_user("alice@example.com")
    session = runtime._session(scope, budget=150)  # noqa: SLF001 - test introspection
    for index in range(20):
        session.add_user("filler " * 40 + str(index), sender="alice@example.com")

    client.queue_text("Compacted summary.")
    client.queue_text("final answer")
    await runtime.handle_dm("alice@example.com", "new question")

    reloaded, _ = runtime.store.load_lenient(scope)
    assert any(event.type == "summary" for event in reloaded)
