"""Agent runtime: authorization, sessions, commands and the agentic loop.

This is the transport-agnostic core. It receives plain ``(sender, text)`` events
from an :class:`~agent.xmpp.transport.XMPPTransport` and returns reply text.
Nothing here imports Slixmpp.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from agent.config.models import AppConfig, RoomConfig
from agent.i18n import Translator, set_locale
from agent.llm.base import ChatMessage
from agent.llm.router import LLMRouter
from agent.llm.tokenizer import TokenEstimator
from agent.memory.store import DataLayout, FilesystemMemoryStore, FilesystemSessionStore
from agent.memory.summarize import Compactor
from agent.net.attachments import AttachmentBundle, AttachmentHandler
from agent.personas.manager import PersonaManager
from agent.runtime.commands import CommandContext, CommandHandler
from agent.runtime.loop import AgentLoop, LoopOutcome
from agent.runtime.prompts import build_system_message
from agent.runtime.rooms import RoomController
from agent.runtime.sessions import Session
from agent.security.authorization import Authorizer
from agent.security.capabilities import CapabilitySet
from agent.security.confirmation import PendingActionStore, classify_reply
from agent.security.credentials import CredentialStore, EnvCredentialStore, Redactor
from agent.security.ignore_store import GLOBAL_SCOPE, IgnoreStore, scope_for_room
from agent.skills.base import SkillContext
from agent.skills.registry import SkillRegistry

log = logging.getLogger(__name__)

#: Commands any authorized occupant may run inside a room. Administrative,
#: owner-only and developer commands are intentionally excluded.
_PUBLIC_COMMANDS = frozenset(
    {"new", "reset", "forget", "status", "ping", "help", "start", "persona", "skills"}
)


def _is_public_command(text: str) -> bool:
    from agent.runtime.commands import parse_command

    parsed = parse_command(text)
    return parsed is not None and parsed[0] in _PUBLIC_COMMANDS


@dataclass
class UsageCounter:
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, other: UsageCounter) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens


class AgentRuntime:
    """Coordinates security, sessions, commands and the LLM loop."""

    def __init__(
        self,
        config: AppConfig,
        router: LLMRouter,
        skills: SkillRegistry,
        store: FilesystemSessionStore,
        layout: DataLayout,
        *,
        redactor: Redactor | None = None,
        personas: PersonaManager | None = None,
        compactor: Compactor | None = None,
        attachments: AttachmentHandler | None = None,
        credentials: CredentialStore | None = None,
        translator: Translator | None = None,
        rooms: RoomController | None = None,
        ignore_store: IgnoreStore | None = None,
    ) -> None:
        self.config = config
        self.router = router
        self.skills = skills
        self.store = store
        self.layout = layout
        self.redactor = redactor or Redactor()
        self.credentials = credentials or EnvCredentialStore()
        self.ignore_store = ignore_store
        self.translator = translator or set_locale(config.agent.language)

        self.estimator = TokenEstimator(config.llm.model)
        self.memory = FilesystemMemoryStore(layout.memory_dir)
        self.authorizer = Authorizer(config.security, ignore_store=ignore_store)
        self.commands = CommandHandler(config, translator=self.translator)

        self.personas = personas
        self.compactor = compactor
        self.attachments = attachments
        self.rooms = rooms
        self.pending = PendingActionStore()

        self._sessions: dict[str, Session] = {}
        self._usage: dict[str, UsageCounter] = {}

    # -- session management ---------------------------------------------

    def _session(self, scope: str, *, budget: int) -> Session:
        session = self._sessions.get(scope)
        if session is None:
            session = Session.load(scope, self.store, self.estimator, budget=budget)
            self._sessions[scope] = session
        return session

    def dm_scope(self, jid: str) -> str:
        return self.layout.scope_for_user(jid)

    def room_scope(self, room_jid: str) -> str:
        return self.layout.scope_for_room(room_jid)

    @staticmethod
    def ignore_scope(room_jid: str) -> str:
        return scope_for_room(room_jid)

    def usage_for(self, scope: str) -> UsageCounter:
        return self._usage.setdefault(scope, UsageCounter())

    # -- capability context ---------------------------------------------

    def _skill_context(self, jid: str, scope: str, *, room_jid: str | None = None) -> SkillContext:
        # Capabilities are resolved per skill at execution time by the registry;
        # the context intentionally starts with an empty grant set.
        return SkillContext(
            jid=jid,
            capabilities=CapabilitySet(),
            redactor=self.redactor,
            room_jid=room_jid,
            session_id=scope,
            credential_store=self.credentials,
            personas=self.personas,
            data={"available_skills": [r.name for r in self.skills.all()]},
        )

    # -- DM handling -----------------------------------------------------

    async def handle_dm(
        self,
        jid: str,
        text: str,
        *,
        message_id: str | None = None,
        attachment_urls: list[str] | None = None,
    ) -> str | None:
        """Handle a direct message. Returns reply text or ``None`` if ignored."""
        decision = self.authorizer.check(jid)
        if not decision.allowed:
            log.info("Ignoring DM from unauthorized %s (%s)", jid, decision.reason)
            return None

        scope = self.dm_scope(jid)
        session = self._session(scope, budget=self.config.llm.dm_history_token_budget)

        confirmation = await self._resolve_confirmation(text, scope, jid, room=None)
        if confirmation is not None:
            return confirmation

        command_result = await self._maybe_command(text, jid, session, scope, room=None)
        if command_result is not None:
            return command_result

        bundle = await self._process_attachments(attachment_urls)
        try:
            text = self._merge_attachment_text(text, bundle)
            session.add_user(text, sender=jid, message_id=message_id)
            outcome = await self._run_loop(
                session,
                jid,
                scope,
                room=None,
                extra_instructions="",
                images=bundle.images if bundle is not None else None,
            )
            self._record_usage(scope, outcome)
            prompt = self._offer_confirmation(outcome, scope, jid)
            if prompt is not None:
                session.add_assistant(prompt)
                return prompt
            session.add_assistant(outcome.content)
            return outcome.content
        finally:
            if self.attachments is not None and bundle is not None:
                self.attachments.cleanup(bundle)

    # -- MUC handling ----------------------------------------------------

    async def handle_muc(
        self,
        room: RoomConfig,
        sender_jid: str,
        text: str,
        *,
        mentioned: bool,
        message_id: str | None = None,
        attachment_urls: list[str] | None = None,
        anonymous: bool = False,
        affiliation: str = "",
        nick: str = "",
    ) -> str | None:
        """Handle a MUC message. Returns reply text or ``None`` if ignored."""
        if self.authorizer.is_ignored(sender_jid, nick=nick, scope=self.ignore_scope(room.jid)):
            log.info("Ignoring MUC message from ignored sender %s/%s", room.jid, nick or sender_jid)
            return None
        decision = self.authorizer.check_room(room, sender_jid, anonymous=anonymous, nick=nick)
        if not decision.allowed:
            log.info(
                "Ignoring MUC message from unauthorized %s in %s (%s)",
                sender_jid,
                room.jid,
                decision.reason,
            )
            return None

        scope = self.room_scope(room.jid)
        session = self._session(
            scope,
            budget=room.history_token_budget or self.config.llm.muc_history_token_budget,
        )

        confirmation = await self._resolve_confirmation(text, scope, sender_jid, room=room)
        if confirmation is not None:
            return confirmation

        # Commands are handled before the mention gate so they work without a
        # mention. Room owners/admins may run any command (e.g. "/mode all");
        # ordinary occupants may run the safe user-facing commands.
        is_privileged = self.authorizer.is_dev(sender_jid) or affiliation in {"owner", "admin"}
        if is_privileged or _is_public_command(text):
            command_result = await self._maybe_command(
                text, sender_jid, session, scope, room=room, affiliation=affiliation
            )
            if command_result is not None:
                return command_result

        if room.activation == "mention" and not mentioned:
            self._record_room_context(room, sender_jid, text, message_id)
            return None

        bundle = await self._process_attachments(attachment_urls)
        try:
            text = self._merge_attachment_text(text, bundle)
            session.add_user(text, sender=sender_jid, message_id=message_id)
            extra = (
                f"The message that triggered you was from {sender_jid}. "
                "Address them by name if useful."
            )
            outcome = await self._run_loop(
                session,
                sender_jid,
                scope,
                room=room,
                extra_instructions=extra,
                images=bundle.images if bundle is not None else None,
            )
            self._record_usage(scope, outcome)
            prompt = self._offer_confirmation(outcome, scope, sender_jid)
            if prompt is not None:
                session.add_assistant(prompt)
                return prompt
            session.add_assistant(outcome.content)
            return outcome.content
        finally:
            if self.attachments is not None and bundle is not None:
                self.attachments.cleanup(bundle)

    def _record_room_context(
        self,
        room: RoomConfig,
        sender_jid: str,
        text: str,
        message_id: str | None,
    ) -> None:
        scope = self.room_scope(room.jid)
        session = self._session(
            scope,
            budget=room.history_token_budget or self.config.llm.muc_history_token_budget,
        )
        session.add_user(text, sender=sender_jid, message_id=message_id)

    # -- attachments -----------------------------------------------------

    async def _process_attachments(self, urls: list[str] | None) -> AttachmentBundle | None:
        if not urls or self.attachments is None or not self.config.attachments.enabled:
            return None
        try:
            return await self.attachments.process(urls)
        except Exception:  # noqa: BLE001 - attachments must not break the turn
            log.exception("Attachment processing failed")
            return None

    @staticmethod
    def _merge_attachment_text(text: str, bundle: AttachmentBundle | None) -> str:
        if bundle is None or not bundle.attachments:
            return text
        summary = bundle.text_summary()
        return f"{text}\n\n{summary}" if text else summary

    # -- shared helpers --------------------------------------------------

    async def _run_loop(
        self,
        session: Session,
        jid: str,
        scope: str,
        *,
        room: RoomConfig | None,
        extra_instructions: str,
        images: list[str] | None = None,
        confirmations_required: set[str] | None = None,
    ) -> LoopOutcome:
        await self._maybe_compact(session)

        persona_text = ""
        persona_skills: set[str] | None = None
        tier = "standard"
        if self.personas is not None:
            persona = (
                self.personas.resolve_room(room.jid)
                if room is not None
                else self.personas.resolve(jid)
            )
            persona_text = persona.render()
            if persona.skills:
                persona_skills = set(persona.skills)
            tier = persona.tier or "standard"

        if images and self._has_tier("vision"):
            tier = "vision"

        long_term_memory = self._long_term_memory(jid, room)
        system = build_system_message(
            self.config,
            room=room,
            persona_text=persona_text,
            long_term_memory=long_term_memory,
            extra_instructions=extra_instructions,
        )
        context = session.build_context()
        if images:
            context = _attach_images(context, images)
        messages: list[ChatMessage] = [system, *context]

        loop = AgentLoop(
            self.router,
            self.skills,
            max_tool_rounds=self.config.agent.max_tool_rounds,
            tier=tier,
        )
        skill_context = self._skill_context(jid, scope, room_jid=room.jid if room else None)
        allowed = self._allowed_skills(room, persona_skills)
        try:
            return await loop.run(
                messages,
                skill_context,
                allowed_skills=allowed,
                confirmations_required=confirmations_required,
            )
        except Exception as exc:  # noqa: BLE001 - never crash the transport
            log.exception("Agent loop failed for %s", jid)
            return LoopOutcome(
                content=self.redactor.redact(self.translator.t("runtime.error", error=exc))
            )

    # -- confirmation flow (SPEC §51) ------------------------------------

    async def _resolve_confirmation(
        self, text: str, scope: str, jid: str, *, room: RoomConfig | None
    ) -> str | None:
        """Handle a reply to a pending confirmation.

        Returns the reply to send, or ``None`` when there is no pending action
        or the message is unrelated (which clears the pending action and lets
        normal processing continue).
        """
        action = self.pending.peek(scope)
        if action is None:
            return None
        verdict = classify_reply(text)
        if verdict is None:
            self.pending.clear(scope)
            return None
        self.pending.pop(scope)
        if verdict == "no":
            return self.translator.t("runtime.cancelled")
        if action.jid != jid:
            return self.translator.t("runtime.wrong_confirm_jid")
        context = self._skill_context(jid, scope, room_jid=room.jid if room else None)
        result = await self.skills.execute(
            action.skill,
            action.arguments,
            context,
            confirmations_required={action.skill},
        )
        if result.ok:
            return result.content or self.translator.t("runtime.done")
        return self.translator.t("runtime.action_not_completed", error=result.error)

    def _store_pending(self, scope: str, jid: str, outcome: LoopOutcome) -> None:
        execution = outcome.confirmation
        assert execution is not None
        prompt = execution.confirmation_prompt or self.translator.t("runtime.confirm_prompt")
        self.pending.set(
            scope,
            jid=jid,
            skill=execution.call.name,
            arguments=execution.call.arguments,
            prompt=prompt,
        )

    def _offer_confirmation(self, outcome: LoopOutcome, scope: str, jid: str) -> str | None:
        """Store a pending action and return the prompt, or ``None`` if none."""
        execution = outcome.confirmation
        if execution is None:
            return None
        self._store_pending(scope, jid, outcome)
        return execution.confirmation_prompt or self.translator.t("runtime.confirm_prompt")

    async def _maybe_compact(self, session: Session) -> None:
        if self.compactor is None:
            return
        events, result = await self.compactor.compact(session.events, budget=session.budget)
        if result.compacted:
            session.replace_events(events)
            log.info(
                "Compacted session %s: %d events summarised",
                session.scope,
                result.summarised,
            )

    def _long_term_memory(self, jid: str, room: RoomConfig | None) -> str:
        if room is not None:
            key = f"rooms/{self.layout.room_dir(room.jid).name}/memory.md"
        else:
            key = f"users/{self.layout.user_dir(jid).name}/memory.md"
        try:
            return self.memory.read(key).strip()
        except Exception:  # noqa: BLE001 - memory must never break a turn
            log.exception("Failed to read long-term memory %s", key)
            return ""

    def _has_tier(self, tier: str) -> bool:
        return bool(getattr(self.config.llm.tiers, tier, ""))

    #: Tools always offered regardless of the active persona's skill list, so a
    #: bounded persona can still create a new one.
    _ALWAYS_ALLOWED_SKILLS = frozenset({"persona_create"})

    def _allowed_skills(
        self, room: RoomConfig | None, persona_skills: set[str] | None
    ) -> set[str] | None:
        configured = set(self.config.skills.enabled)
        allowed: set[str] | None = configured or None
        if persona_skills is not None:
            merged = set(persona_skills) | self._ALWAYS_ALLOWED_SKILLS
            allowed = merged if allowed is None else allowed & merged
        return allowed

    def _record_usage(self, scope: str, outcome: LoopOutcome) -> None:
        counter = self.usage_for(scope)
        counter.input_tokens += outcome.usage.input_tokens
        counter.output_tokens += outcome.usage.output_tokens

    # -- commands --------------------------------------------------------

    async def _maybe_command(
        self,
        text: str,
        jid: str,
        session: Session,
        scope: str,
        *,
        room: RoomConfig | None,
        affiliation: str = "",
    ) -> str | None:
        usage = self.usage_for(scope)
        context = CommandContext(
            session=session,
            config=self.config,
            jid=jid,
            is_dev=self.authorizer.is_dev(jid),
            tier="standard",
            usage_input=usage.input_tokens,
            usage_output=usage.output_tokens,
            personas=(
                _PersonaCommandAdapter(
                    self.personas, room_jid=room.jid if room is not None else None
                )
                if self.personas is not None
                else None
            ),
            active_persona=(
                self.personas.active_room_persona(room.jid)
                if room is not None and self.personas is not None
                else (self.personas.active_persona(jid) if self.personas is not None else "")
            ),
            available_skills=[registration.name for registration in self.skills.all()],
            rooms=self.rooms,
            current_room=room,
            affiliation=affiliation,
            ignore_store=self.ignore_store,
            ignore_scope=(self.ignore_scope(room.jid) if room is not None else GLOBAL_SCOPE),
        )
        result = self.commands.dispatch(text, context)
        if result is None or not result.handled:
            return None
        if result.new_session:
            session.reset()
        if result.forget_memory:
            self._forget(session, jid, room)
        reply = result.reply
        if result.join_room is not None:
            reply = await self._join_room(result.join_room)
        elif result.leave_room is not None:
            reply = await self._leave_room(result.leave_room)
        elif result.set_room_mode is not None:
            room_jid, mode = result.set_room_mode
            self._set_room_mode(room_jid, mode)
        return reply

    def _set_room_mode(self, room_jid: str, mode: str) -> None:
        if self.rooms is None:
            return
        try:
            self.rooms.set_room_mode(room_jid, mode)
        except Exception:  # noqa: BLE001 - report, never crash
            log.exception("Failed to set mode for room %s", room_jid)

    async def _join_room(self, room: RoomConfig) -> str:
        if self.rooms is None:
            return self.translator.t("room.unavailable")
        try:
            await self.rooms.join_room(room)
        except Exception as exc:  # noqa: BLE001 - report, never crash
            log.exception("Failed to join room %s", room.jid)
            return self.translator.t("room.join_failed", room=room.jid, error=exc)
        return self.translator.t("room.joined", room=room.jid, nick=room.nick)

    async def _leave_room(self, room_jid: str) -> str:
        if self.rooms is None:
            return self.translator.t("room.unavailable")
        try:
            await self.rooms.leave_room(room_jid)
        except Exception as exc:  # noqa: BLE001
            log.exception("Failed to leave room %s", room_jid)
            return self.translator.t("room.leave_failed", room=room_jid, error=exc)
        return self.translator.t("room.left", room=room_jid)

    def _forget(self, session: Session, jid: str, room: RoomConfig | None) -> None:
        if room is not None:
            key = f"rooms/{self.layout.room_dir(room.jid).name}/memory.md"
        else:
            key = f"users/{self.layout.user_dir(jid).name}/memory.md"
        try:
            self.memory.write(key, "")
        except Exception:  # noqa: BLE001 - forget must not fail the command
            log.exception("Failed to clear memory for %s", key)

    async def aclose(self) -> None:
        await self.router.aclose()

    def _personas_enabled(self) -> bool:
        return self.personas is not None


def _attach_images(context: list[ChatMessage], images: list[str]) -> list[ChatMessage]:
    """Attach images to the most recent user message."""
    for message in reversed(context):
        if message.role == "user":
            message.images = list(images)
            return context
    context.append(ChatMessage(role="user", content="", images=list(images)))
    return context


class _PersonaCommandAdapter:
    """Narrow view of :class:`PersonaManager` used by the command handler.

    The command handler addresses personas by a ``jid`` string. In a DM that is
    the user's JID; in a room it is the room JID, and the adapter routes every
    call to the room-scoped manager methods.
    """

    def __init__(self, manager: PersonaManager, *, room_jid: str | None = None) -> None:
        self.manager = manager
        self.room_jid = room_jid

    def list(self, jid: str) -> list[str]:
        if self.room_jid is not None:
            return self.manager.list_room_personas(self.room_jid)
        return self.manager.list_personas(jid)

    def active(self, jid: str) -> str:
        if self.room_jid is not None:
            return self.manager.active_room_persona(self.room_jid)
        return self.manager.active_persona(jid)

    def select(self, jid: str, name: str) -> None:
        if self.room_jid is not None:
            self.manager.set_room_active_persona(self.room_jid, name)
        else:
            self.manager.set_active_persona(jid, name)

    def reset(self, jid: str) -> None:
        if self.room_jid is not None:
            self.manager.reset_room_active_persona(self.room_jid)
        else:
            self.manager.reset_active_persona(jid)

    def create(self, jid: str, name: str, description: str = "") -> None:
        if self.room_jid is not None:
            self.manager.create_room_persona(self.room_jid, name, description=description)
        else:
            self.manager.create_user_persona(jid, name, description=description)

    def delete(self, jid: str, name: str) -> None:
        if self.room_jid is not None:
            self.manager.delete_room_persona(self.room_jid, name)
        else:
            self.manager.delete_user_persona(jid, name)

    def export(self, jid: str, name: str) -> str:
        if self.room_jid is not None:
            return self.manager.export_room_persona(self.room_jid, name)
        return self.manager.export_user_persona(jid, name)

    def import_(self, jid: str, name: str, payload: str) -> None:
        if self.room_jid is not None:
            self.manager.import_room_persona(self.room_jid, name, payload)
        else:
            self.manager.import_user_persona(jid, name, payload)
