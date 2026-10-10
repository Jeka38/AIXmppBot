"""Application wiring: connects the XMPP transport to the agent runtime.

This is the only module that knows about both sides. It translates transport
events into runtime calls, manages chat states (XEP-0085) and owns the
high-level lifecycle.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from agent.config.models import AppConfig, RoomConfig
from agent.errors import AgentError, ConfigError
from agent.llm.router import LLMRouter
from agent.llm.tokenizer import TokenEstimator
from agent.memory.store import DataLayout, FilesystemMemoryStore, FilesystemSessionStore
from agent.memory.summarize import Compactor
from agent.net.attachments import AttachmentHandler
from agent.net.fetch import SafeFetcher
from agent.net.paste import PasteError, PasteService
from agent.personas.manager import PersonaManager
from agent.runtime.commands import parse_command
from agent.runtime.rooms import RoomStore
from agent.runtime.runtime import AgentRuntime
from agent.search import build_search_provider
from agent.search.base import SearchProvider
from agent.security.credentials import EnvCredentialStore, Redactor
from agent.security.policy import CapabilityPolicy
from agent.skills.registry import ConfirmationPolicy, SkillRegistry
from agent.xmpp.base import BaseTransport
from agent.xmpp.events import MessageEvent, MUCMessageEvent, PresenceEvent

log = logging.getLogger(__name__)


class AgentApplication:
    """Owns the runtime and transport for one configured agent instance."""

    def __init__(
        self,
        config: AppConfig,
        *,
        data_dir: str | None = None,
        global_persona_dirs: list[Path] | None = None,
    ) -> None:
        self.config = config
        self.redactor = Redactor()
        self.credentials = EnvCredentialStore()
        self._register_secrets()

        layout = DataLayout(_resolve_data_dir(config, data_dir))
        store = FilesystemSessionStore(layout.memory_dir)
        router = LLMRouter(config.llm)
        policy = CapabilityPolicy.from_config(config.skills.capabilities)
        confirmations = ConfirmationPolicy(
            required={name: True for name, flag in config.security.confirmations.items() if flag}
        )
        skills = SkillRegistry(redactor=self.redactor, policy=policy, confirmations=confirmations)
        self.layout = layout
        self.fetcher = SafeFetcher(
            max_bytes=_max_fetch_bytes(config),
            timeout=config.attachments.download_timeout,
        )
        self.paste = (
            PasteService(
                config.paste.url,
                field=config.paste.field,
                token=config.paste.token,
                expiration=config.paste.expiration,
                privacy=config.paste.privacy,
                timeout=config.paste.timeout,
            )
            if config.paste.enabled and config.paste.url
            else None
        )
        self.personas = PersonaManager(
            layout,
            config.personas,
            global_dirs=global_persona_dirs or [Path("config/personas"), layout.personas_dir],
        )
        self.attachments = (
            AttachmentHandler(
                fetcher=self.fetcher,
                max_size_mb=config.attachments.max_size_mb,
                temp_dir=config.attachments.temp_dir or None,
            )
            if config.attachments.enabled
            else None
        )
        compactor = Compactor(router, self._estimator_for(router))
        self._register_skills(skills)

        self.skills = skills
        self.room_store = RoomStore(layout.state_dir / "rooms.json")

        # Imported lazily so the runtime core never hard-depends on Slixmpp at
        # import time (keeps it testable without a transport).
        from agent.xmpp import build_transport

        self.transport: BaseTransport = build_transport(config)
        for room in self.room_store.load():
            self.transport.register_room(room)

        self.runtime = AgentRuntime(
            config,
            router,
            skills,
            store,
            layout,
            redactor=self.redactor,
            personas=self.personas,
            compactor=compactor,
            attachments=self.attachments,
            credentials=self.credentials,
            rooms=self,
        )
        self._wire_transport()
        self._stop = asyncio.Event()

    # -- setup -----------------------------------------------------------

    @staticmethod
    def _estimator_for(router: LLMRouter) -> TokenEstimator:
        from agent.llm.tokenizer import TokenEstimator

        return TokenEstimator(router.config.model)

    def _register_secrets(self) -> None:
        self.redactor.register(
            self.config.llm.api_key,
            self.config.xmpp.password,
            self.config.xmpp.secret,
            self.config.paste.token,
        )
        for skill_config in (
            self.config.skills.web_search,
            self.config.skills.github,
        ):
            for key in ("api_key", "token"):
                value = skill_config.get(key)
                if isinstance(value, str):
                    self.redactor.register(value)

    def _register_skills(self, registry: SkillRegistry) -> None:
        from agent.security.capabilities import CapabilityGrant
        from agent.skills.memory import MemoryRecallSkill, MemoryStoreSkill
        from agent.skills.persona import PersonaCreateSkill
        from agent.skills.url_fetch import UrlFetchSkill
        from agent.skills.web_search import WebSearchSkill

        memory = self.runtime_memory()
        registry.register(MemoryStoreSkill(memory))
        registry.register(MemoryRecallSkill(memory))
        # Lets the model create personas from a natural-language role.
        registry.register(
            PersonaCreateSkill(),
            grants=[CapabilityGrant.parse("filesystem")],
        )
        # url_fetch may reach any public host (SSRF still applies); a deployment
        # can narrow this via [skills.capabilities.url_fetch] without code changes.
        registry.register(
            UrlFetchSkill(self.fetcher),
            grants=[CapabilityGrant.parse("network")],
        )

        search_config = self.config.skills.web_search
        if search_config.get("enabled"):
            provider = _build_search_provider(search_config)
            if provider is not None:
                max_results = int(search_config.get("max_results", 5) or 5)
                registry.register(
                    WebSearchSkill(provider, max_results=max_results),
                    grants=[CapabilityGrant.parse("network")],
                )

    def runtime_memory(self) -> FilesystemMemoryStore:
        return FilesystemMemoryStore(self.layout.memory_dir)

    def _wire_transport(self) -> None:
        self.transport.on_message = self._handle_dm
        self.transport.on_muc_message = self._handle_muc
        self.transport.on_presence = self._handle_presence
        self.transport.on_connected = self._handle_connected
        self.transport.on_disconnected = self._handle_disconnected
        if self.config.security.auto_authorize_subscriptions:
            self.transport.subscription_decider = self._allow_subscription

    def _allow_subscription(self, jid: str) -> bool:
        """Approve roster subscription only for allowlisted senders."""
        return self.runtime.authorizer.is_authorized(jid)

    # -- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        log.info("Connecting to XMPP as %s", self.config.xmpp.effective_jid())
        await self.transport.connect()
        log.info("Agent is online")

    async def run_forever(self) -> None:
        await self.start()
        await self._stop.wait()

    async def stop(self) -> None:
        self._stop.set()
        try:
            await self.transport.disconnect()
        except Exception:  # noqa: BLE001 - shutdown must be best effort
            log.exception("Error during transport shutdown")
        await self.runtime.aclose()
        await self.fetcher.aclose()
        if self.paste is not None:
            await self.paste.aclose()

    # -- transport callbacks ---------------------------------------------

    async def _handle_connected(self) -> None:
        log.info("XMPP session ready for %s", self.config.xmpp.effective_jid())

    async def _handle_disconnected(self) -> None:
        log.warning("XMPP connection lost; Slixmpp will attempt to reconnect")

    # -- RoomController (runtime join/leave) -----------------------------

    async def join_room(self, room: RoomConfig) -> None:
        """Join a MUC room at runtime and persist it for rejoin after restart."""
        self.transport.register_room(room)
        self.room_store.add(room)
        await self.transport.join_muc(room.jid, room.nick, password=room.password)

    async def leave_room(self, room_jid: str) -> None:
        room = self.transport.room_for(room_jid)
        nick = room.nick if room else self.config.xmpp.nickname
        await self.transport.leave_muc(room_jid, nick, status="Leaving")
        self.transport.unregister_room(room_jid)
        self.room_store.remove(room_jid)

    def set_room_mode(self, room_jid: str, mode: str) -> None:
        """Persist a room's activation mode (owner command from the room)."""
        room = self.transport.room_for(room_jid)
        if room is None:
            return
        updated = room.model_copy(update={"activation": mode})
        self.transport.register_room(updated)
        self.room_store.add(updated)

    def list_rooms(self) -> list[RoomConfig]:
        return self.transport.rooms()

    async def _handle_dm(self, event: MessageEvent) -> None:
        jid = event.sender_jid
        if not self.runtime.authorizer.is_authorized(jid):
            log.info("Dropping unauthorized DM from %s", jid)
            return

        is_command = parse_command(event.text) is not None
        if not is_command:
            await self._safe_chat_state(jid, "composing")
        try:
            reply = await self.runtime.handle_dm(
                jid,
                event.text,
                message_id=event.message_id,
                attachment_urls=event.attachments,
            )
        except AgentError as exc:
            log.warning("Agent error handling DM from %s: %s", jid, exc)
            await self._safe_chat_state(jid, "paused")
            await self._send_dm(jid, self.redactor.redact(f"Error: {exc}"))
            return
        except Exception as exc:  # noqa: BLE001 - one bad message must not kill us
            log.exception("Unexpected error handling DM from %s", jid)
            await self._safe_chat_state(jid, "paused")
            await self._send_dm(jid, self.redactor.redact(f"Unexpected error: {exc}"))
            return

        if reply is None:
            await self._safe_chat_state(jid, "paused")
            return
        await self._safe_chat_state(jid, "active")
        await self._send_dm(jid, reply)

    async def _handle_muc(self, room: RoomConfig, event: MUCMessageEvent) -> None:
        try:
            reply = await self.runtime.handle_muc(
                room,
                event.sender_jid,
                event.text,
                mentioned=event.mentioned,
                message_id=event.message_id,
                attachment_urls=event.attachments,
                anonymous=event.anonymous,
                affiliation=event.affiliation,
                nick=event.nick,
            )
        except AgentError as exc:
            log.warning("Agent error handling MUC message in %s: %s", room.jid, exc)
            await self._send_muc(room.jid, self.redactor.redact(f"Error: {exc}"))
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("Unexpected error handling MUC message in %s", room.jid)
            await self._send_muc(room.jid, self.redactor.redact(f"Unexpected error: {exc}"))
            return
        if reply is not None:
            await self._send_muc(room.jid, reply)

    async def _handle_presence(self, event: PresenceEvent) -> None:
        log.debug("Presence: %s available=%s", event.jid, event.available)

    # -- outbound helpers ------------------------------------------------

    async def _send_dm(self, jid: str, body: str) -> None:
        try:
            await self.transport.send_message(jid, await self._maybe_paste(body))
        except Exception:  # noqa: BLE001
            log.exception("Failed to send DM to %s", jid)

    async def _send_muc(self, room_jid: str, body: str) -> None:
        try:
            await self.transport.send_muc_message(room_jid, await self._maybe_paste(body))
        except Exception:  # noqa: BLE001
            log.exception("Failed to send MUC message to %s", room_jid)

    async def _maybe_paste(self, body: str) -> str:
        """Upload a long answer to the paste service and return a link message.

        Short answers (at or below the threshold) are returned unchanged. If the
        upload fails, the original text is sent so the user still gets an answer.
        """
        config = self.config.paste
        if self.paste is None or len(body) <= config.threshold:
            return body
        try:
            url = await self.paste.upload(body)
        except PasteError as exc:
            log.warning("Paste upload failed, sending full text: %s", exc)
            return body
        return self._render_paste_notice(url)

    def _render_paste_notice(self, url: str) -> str:
        from agent.i18n import get_translator

        return get_translator().t("paste.notice", url=url)

    async def _safe_chat_state(self, jid: str, state: str) -> None:
        try:
            await self.transport.send_chat_state(jid, state)
        except Exception:  # noqa: BLE001 - chat states are best effort
            log.debug("Failed to send chat state %s to %s", state, jid)


def _resolve_data_dir(config: AppConfig, override: str | None) -> Path:
    return Path(override or config.storage.data_dir)


def _build_search_provider(config: dict[str, Any]) -> SearchProvider | None:
    try:
        return build_search_provider(config)
    except ConfigError as exc:
        log.error("Web search disabled: %s", exc)
        return None


def _max_fetch_bytes(config: AppConfig) -> int:
    return max(1, config.attachments.max_size_mb) * 1024 * 1024


def startup_report(config: AppConfig, skills: SkillRegistry | None = None) -> str:
    """Render the SPEC §60 startup summary without leaking secrets."""
    lines = [
        "Fluux Python Agent",
        "==================",
        "",
        f"XMPP mode: {config.xmpp.mode}",
        f"JID: {config.xmpp.effective_jid()}",
        f"LLM provider: {config.llm.provider}",
        f"LLM model: {config.llm.model or '(per-tier)'}",
    ]
    if skills is not None:
        enabled = sum(1 for registration in skills.all() if registration.enabled)
        lines.append(f"Skills: {enabled}")
    lines.extend(
        [
            f"Personas: {config.personas.default_persona} (default)",
            f"MUC rooms: {len(config.rooms)}",
            "Memory: enabled",
            f"Web search: {'enabled' if config.skills.web_search.get('enabled') else 'disabled'}",
            f"Attachments: {'enabled' if config.attachments.enabled else 'disabled'}",
        ]
    )
    return "\n".join(lines)
