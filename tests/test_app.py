"""Application wiring: chat states, transport callbacks and startup report."""

from __future__ import annotations

from agent.app import AgentApplication, startup_report
from agent.config.models import RoomConfig
from agent.xmpp.events import MessageEvent, MUCMessageEvent
from tests.conftest import make_config


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []  # (kind, to, body)
        self.chat_states: list[tuple[str, str]] = []
        self.muc: list[tuple[str, str]] = []

    async def send_message(self, to_jid: str, body: str, **kwargs) -> None:
        self.sent.append(("dm", to_jid, body))

    async def send_chat_state(self, to_jid: str, state: str) -> None:
        self.chat_states.append((to_jid, state))

    async def send_muc_message(self, room_jid: str, body: str) -> None:
        self.muc.append((room_jid, body))

    async def send_presence(self, **kwargs) -> None:
        pass


def _app(tmp_path, **overrides) -> AgentApplication:
    config = make_config(**overrides)
    return AgentApplication(config, data_dir=str(tmp_path))


async def test_dm_sends_composing_then_active(tmp_path, monkeypatch) -> None:
    app = _app(tmp_path)
    fake = FakeTransport()
    app.transport = fake

    async def fake_handle_dm(jid, text, *, message_id=None, attachment_urls=None):
        return "the answer"

    monkeypatch.setattr(app.runtime, "handle_dm", fake_handle_dm)

    await app._handle_dm(MessageEvent(sender_jid="alice@example.com", text="hi"))

    assert fake.chat_states[0] == ("alice@example.com", "composing")
    assert fake.chat_states[-1] == ("alice@example.com", "active")
    assert fake.sent[-1] == ("dm", "alice@example.com", "the answer")


async def test_command_does_not_send_composing(tmp_path, monkeypatch) -> None:
    app = _app(tmp_path)
    fake = FakeTransport()
    app.transport = fake

    async def fake_handle_dm(jid, text, *, message_id=None, attachment_urls=None):
        return "pong"

    monkeypatch.setattr(app.runtime, "handle_dm", fake_handle_dm)

    await app._handle_dm(MessageEvent(sender_jid="alice@example.com", text="/ping"))

    # Slash commands must not emit a composing state (SPEC §5).
    assert all(state[1] != "composing" for state in fake.chat_states)


async def test_unauthorized_dm_never_reaches_runtime(tmp_path) -> None:
    app = _app(tmp_path)
    fake = FakeTransport()
    app.transport = fake

    await app._handle_dm(MessageEvent(sender_jid="mallory@evil.com", text="hi"))

    assert fake.sent == []
    assert fake.chat_states == []


async def test_muc_reply_sent_to_room(tmp_path, monkeypatch) -> None:
    app = _app(tmp_path)
    fake = FakeTransport()
    app.transport = fake
    room = RoomConfig(jid="room@conference.example.com", nick="Agent")

    async def fake_handle_muc(
        room_arg,
        jid,
        text,
        *,
        mentioned,
        message_id=None,
        attachment_urls=None,
        anonymous=False,
        affiliation="",
        nick="",
    ):
        return "room answer"

    monkeypatch.setattr(app.runtime, "handle_muc", fake_handle_muc)

    event = MUCMessageEvent(
        room_jid=room.jid,
        sender_jid="alice@example.com",
        nick="Alice",
        text="@Agent hi",
        mentioned=True,
    )
    await app._handle_muc(room, event)

    assert fake.muc == [("room@conference.example.com", "room answer")]


def test_startup_report_hides_secrets(tmp_path) -> None:
    config = make_config()
    config.llm.api_key = "super-secret-key"
    report = startup_report(config)
    assert "super-secret-key" not in report
    assert "c2s" in report
