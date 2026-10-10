"""Slash commands, intercepted before the LLM (SPEC §17).

Commands never reach the model as user text. Developer commands require a JID
in ``security.dev_command_jids``; ``/raw`` additionally requires
``security.allow_raw_xml``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from agent.config.models import AppConfig, RoomConfig
from agent.i18n import DEFAULT_LOCALE, Translator, get_translator
from agent.runtime.rooms import RoomController
from agent.runtime.sessions import Session
from agent.security.ignore_store import GLOBAL_SCOPE, IgnoreStore, classify_target


class PersonaCommands(Protocol):
    """Persona operations the command handler needs (avoids a hard dependency)."""

    def list(self, jid: str) -> list[str]: ...

    def active(self, jid: str) -> str: ...

    def select(self, jid: str, name: str) -> None: ...

    def reset(self, jid: str) -> None: ...


@dataclass
class CommandContext:
    session: Session
    config: AppConfig
    jid: str
    is_dev: bool = False
    tier: str = "standard"
    usage_input: int = 0
    usage_output: int = 0
    personas: PersonaCommands | None = None
    active_persona: str = ""
    available_skills: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    translator: Translator | None = None
    #: Runtime MUC control (join/leave/list). ``None`` in a DM-only context.
    rooms: RoomController | None = None
    #: MUC room the command was issued from, if any (commands are DM-only when
    #: ``None``).
    current_room: RoomConfig | None = None
    #: The sender's MUC affiliation in ``current_room`` (owner/admin/...).
    affiliation: str = ""
    #: Runtime ignore-list store (``/ignore``). ``None`` disables the commands.
    ignore_store: IgnoreStore | None = None
    #: Ignore scope for ``/ignore``: a room scope when in a room, else global.
    ignore_scope: str = GLOBAL_SCOPE

    def tr(self, key: str, /, **kwargs: object) -> str:
        translator = self.translator or get_translator()
        return translator.t(key, **kwargs)


@dataclass
class CommandResult:
    handled: bool
    reply: str = ""
    new_session: bool = False
    forget_memory: bool = False
    #: Room the runtime should join after this command completes.
    join_room: RoomConfig | None = None
    #: Room JID the runtime should leave after this command completes.
    leave_room: str | None = None
    #: ``(room_jid, mode)`` the runtime should persist after this command.
    set_room_mode: tuple[str, str] | None = None


def parse_command(text: str) -> tuple[str, list[str]] | None:
    """Return ``(name, args)`` for a leading slash command, else ``None``."""
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    parts = stripped[1:].split()
    if not parts:
        return None
    return parts[0].lower(), parts[1:]


class CommandHandler:
    """Dispatches slash commands against a :class:`CommandContext`."""

    def __init__(self, config: AppConfig, *, translator: Translator | None = None) -> None:
        self.config = config
        self.translator = translator or Translator(DEFAULT_LOCALE)

    def dispatch(self, text: str, context: CommandContext) -> CommandResult | None:
        parsed = parse_command(text)
        if parsed is None:
            return None
        name, args = parsed
        context.translator = context.translator or self.translator
        return self._run(name, args, context)

    def _run(self, name: str, args: list[str], context: CommandContext) -> CommandResult:
        handler = {
            "new": self._cmd_new,
            "reset": self._cmd_reset,
            "forget": self._cmd_forget,
            "status": self._cmd_status,
            "ping": self._cmd_ping,
            "help": self._cmd_help,
            "start": self._cmd_help,
            "persona": self._cmd_persona,
            "skills": self._cmd_skills,
            "skill": self._cmd_skill,
            "join": self._cmd_join,
            "leave": self._cmd_leave,
            "rooms": self._cmd_rooms,
            "mode": self._cmd_mode,
            "ignore": self._cmd_ignore,
            "unignore": self._cmd_unignore,
            "debug": self._cmd_debug,
            "context": self._cmd_context,
            "history": self._cmd_history,
            "tier": self._cmd_tier,
            "usage": self._cmd_usage,
            "raw": self._cmd_raw,
        }.get(name)
        if handler is None:
            return CommandResult(handled=True, reply=context.tr("command.unknown", name=name))
        return handler(args, context)

    # -- user commands ---------------------------------------------------

    def _cmd_new(self, args: list[str], context: CommandContext) -> CommandResult:
        return CommandResult(
            handled=True,
            reply=context.tr("command.new"),
            new_session=True,
        )

    def _cmd_reset(self, args: list[str], context: CommandContext) -> CommandResult:
        return CommandResult(handled=True, reply=context.tr("command.reset"), new_session=True)

    def _cmd_forget(self, args: list[str], context: CommandContext) -> CommandResult:
        return CommandResult(
            handled=True,
            reply=context.tr("command.forget"),
            new_session=True,
            forget_memory=True,
        )

    def _cmd_status(self, args: list[str], context: CommandContext) -> CommandResult:
        lines = [
            context.tr("status.title", name=self.config.agent.name),
            context.tr("status.scope", scope=context.session.scope),
            context.tr("status.messages", count=context.session.message_count()),
            context.tr(
                "status.persona",
                persona=context.active_persona or context.tr("status.default"),
            ),
            context.tr("status.tier", tier=context.tier),
        ]
        return CommandResult(handled=True, reply="\n".join(lines))

    def _cmd_ping(self, args: list[str], context: CommandContext) -> CommandResult:
        return CommandResult(handled=True, reply=context.tr("command.ping"))

    def _cmd_help(self, args: list[str], context: CommandContext) -> CommandResult:
        reply = context.tr("command.help.public")
        if context.is_dev:
            reply += context.tr("command.help.admin")
        return CommandResult(handled=True, reply=reply)

    # -- personas --------------------------------------------------------

    def _cmd_persona(self, args: list[str], context: CommandContext) -> CommandResult:
        if context.personas is None:
            return CommandResult(handled=True, reply=context.tr("persona.unavailable"))
        manager = context.personas
        if not args:
            active = manager.active(context.jid)
            return CommandResult(handled=True, reply=context.tr("persona.active", name=active))
        sub = args[0].lower()
        rest = args[1:]
        try:
            if sub == "list":
                names = manager.list(context.jid)
                if not names:
                    return CommandResult(handled=True, reply=context.tr("persona.none"))
                active = manager.active(context.jid)
                lines = [f"{'*' if name == active else ' '} {name}" for name in names]
                header = context.tr("persona.list_header")
                return CommandResult(handled=True, reply=header + "\n" + "\n".join(lines))
            if sub in {"reset", "default"}:
                manager.reset(context.jid)
                return CommandResult(
                    handled=True,
                    reply=context.tr("persona.reset", name=self.config.personas.default_persona),
                )
            if sub == "create":
                if not rest:
                    return CommandResult(handled=True, reply=context.tr("persona.usage.create"))
                self._create_persona(context, rest[0], rest[1:])
                return CommandResult(
                    handled=True, reply=context.tr("persona.created", name=rest[0])
                )
            if sub == "delete":
                if not rest:
                    return CommandResult(handled=True, reply=context.tr("persona.usage.delete"))
                self._delete_persona(context, rest[0])
                return CommandResult(
                    handled=True, reply=context.tr("persona.deleted", name=rest[0])
                )
            if sub == "export":
                if not rest:
                    return CommandResult(handled=True, reply=context.tr("persona.usage.export"))
                return CommandResult(handled=True, reply=self._export_persona(context, rest[0]))
            if sub == "import":
                if len(rest) < 2:
                    return CommandResult(handled=True, reply=context.tr("persona.usage.import"))
                self._import_persona(context, rest[0], " ".join(rest[1:]))
                return CommandResult(
                    handled=True, reply=context.tr("persona.imported", name=rest[0])
                )
        except Exception as exc:  # noqa: BLE001 - report, never crash
            return CommandResult(handled=True, reply=context.tr("persona.error", error=exc))

        # Otherwise: select a persona by name.
        try:
            manager.select(context.jid, sub)
        except Exception as exc:  # noqa: BLE001
            return CommandResult(handled=True, reply=context.tr("persona.error", error=exc))
        return CommandResult(handled=True, reply=context.tr("persona.switched", name=sub))

    def _create_persona(self, context: CommandContext, name: str, rest: list[str]) -> None:
        manager = context.personas
        assert manager is not None
        create = getattr(manager, "create", None)
        if create is None:
            raise RuntimeError("This persona backend does not support creation")
        create(context.jid, name, " ".join(rest))

    def _delete_persona(self, context: CommandContext, name: str) -> None:
        manager = context.personas
        assert manager is not None
        delete = getattr(manager, "delete", None)
        if delete is None:
            raise RuntimeError("This persona backend does not support deletion")
        delete(context.jid, name)

    def _export_persona(self, context: CommandContext, name: str) -> str:
        manager = context.personas
        assert manager is not None
        export = getattr(manager, "export", None)
        if export is None:
            raise RuntimeError("This persona backend does not support export")
        return str(export(context.jid, name))

    def _import_persona(self, context: CommandContext, name: str, payload: str) -> None:
        manager = context.personas
        assert manager is not None
        importer = getattr(manager, "import_", None)
        if importer is None:
            raise RuntimeError("This persona backend does not support import")
        importer(context.jid, name, payload)

    # -- skills ----------------------------------------------------------

    def _cmd_skills(self, args: list[str], context: CommandContext) -> CommandResult:
        names = context.available_skills
        rendered = ", ".join(names) if names else context.tr("skills.none")
        return CommandResult(handled=True, reply=context.tr("skills.enabled", names=rendered))

    def _cmd_skill(self, args: list[str], context: CommandContext) -> CommandResult:
        if not args:
            return CommandResult(handled=True, reply=context.tr("skill.usage"))
        return CommandResult(handled=True, reply=context.tr("skill.restricted"))

    # -- rooms (administrators only) -------------------------------------

    def _cmd_join(self, args: list[str], context: CommandContext) -> CommandResult:
        denied = self._require_dev(context, "join")
        if denied:
            return denied
        if not args:
            return CommandResult(handled=True, reply=context.tr("room.join.usage"))
        room_jid = args[0].strip().lower()
        if "@" not in room_jid:
            return CommandResult(handled=True, reply=context.tr("room.invalid_jid"))
        nick = args[1].strip() if len(args) > 1 else self.config.xmpp.nickname
        activation = "mention"
        if len(args) > 2 and args[2].lower() in {"all", "mention"}:
            activation = args[2].lower()
        room = RoomConfig(jid=room_jid, nick=nick, activation=activation)  # type: ignore[arg-type]
        return CommandResult(
            handled=True,
            reply=context.tr("room.joining", room=room_jid, nick=nick),
            join_room=room,
        )

    def _cmd_leave(self, args: list[str], context: CommandContext) -> CommandResult:
        denied = self._require_dev(context, "leave")
        if denied:
            return denied
        room_jid = args[0].strip().lower() if args else ""
        if not room_jid and context.current_room is not None:
            room_jid = context.current_room.jid.lower()
        if not room_jid:
            return CommandResult(handled=True, reply=context.tr("room.leave.usage"))
        return CommandResult(
            handled=True,
            reply=context.tr("room.leaving", room=room_jid),
            leave_room=room_jid,
        )

    def _cmd_rooms(self, args: list[str], context: CommandContext) -> CommandResult:
        denied = self._require_dev(context, "rooms")
        if denied:
            return denied
        if context.rooms is None:
            return CommandResult(handled=True, reply=context.tr("room.unavailable"))
        rooms = context.rooms.list_rooms()
        if not rooms:
            return CommandResult(handled=True, reply=context.tr("room.none"))
        current = context.current_room.jid.lower() if context.current_room else ""
        lines = [
            f"{'*' if room.jid.lower() == current else ' '} "
            f"{room.jid} (nick: {room.nick}, {room.activation})"
            for room in rooms
        ]
        return CommandResult(
            handled=True,
            reply=context.tr("room.header") + "\n" + "\n".join(lines),
        )

    def _cmd_mode(self, args: list[str], context: CommandContext) -> CommandResult:
        """Change the activation mode of the current room (owner only).

        Usable from inside a public room: affiliation is visible even when the
        real JID is hidden.
        """
        room = context.current_room
        if room is None:
            return CommandResult(handled=True, reply=context.tr("room.mode.not_in_room"))
        if context.affiliation != "owner":
            return CommandResult(handled=True, reply=context.tr("room.owner_only"))
        if not args:
            return CommandResult(
                handled=True,
                reply=context.tr("room.mode.current", room=room.jid, mode=room.activation),
            )
        mode = args[0].strip().lower()
        if mode not in {"mention", "all"}:
            return CommandResult(handled=True, reply=context.tr("room.mode.invalid", mode=mode))
        return CommandResult(
            handled=True,
            reply=context.tr("room.mode.set", room=room.jid, mode=mode),
            set_room_mode=(room.jid, mode),
        )

    # -- ignore list (administrators and room owners) --------------------

    def _ignore_allowed(self, context: CommandContext) -> CommandResult | None:
        """Allow bot admins anywhere, and room owners inside their room."""
        if context.is_dev:
            return None
        if context.current_room is not None and context.affiliation == "owner":
            return None
        return CommandResult(handled=True, reply=context.tr("ignore.denied"))

    def _cmd_ignore(self, args: list[str], context: CommandContext) -> CommandResult:
        denied = self._ignore_allowed(context)
        if denied:
            return denied
        if context.ignore_store is None:
            return CommandResult(handled=True, reply=context.tr("ignore.unavailable"))
        if not args:
            return CommandResult(handled=True, reply=self._render_ignore_list(context))
        kind, value = classify_target(args[0])
        if not value:
            return CommandResult(handled=True, reply=context.tr("ignore.usage"))
        added = context.ignore_store.add(kind, value, scope=context.ignore_scope)
        key = "ignore.added" if added else "ignore.exists"
        return CommandResult(handled=True, reply=context.tr(key, target=value, kind=kind))

    def _cmd_unignore(self, args: list[str], context: CommandContext) -> CommandResult:
        denied = self._ignore_allowed(context)
        if denied:
            return denied
        if context.ignore_store is None:
            return CommandResult(handled=True, reply=context.tr("ignore.unavailable"))
        if not args:
            return CommandResult(handled=True, reply=context.tr("ignore.usage"))
        kind, value = classify_target(args[0])
        removed = context.ignore_store.remove(kind, value, scope=context.ignore_scope)
        key = "ignore.removed" if removed else "ignore.not_found"
        return CommandResult(handled=True, reply=context.tr(key, target=value, kind=kind))

    def _render_ignore_list(self, context: CommandContext) -> str:
        store = context.ignore_store
        lines = [context.tr("ignore.header")]
        # Room-scoped entries first (only meaningful inside a room).
        if context.ignore_scope != GLOBAL_SCOPE and store is not None:
            room_entries = store.entries(context.ignore_scope)
            if room_entries:
                lines.append("  " + context.tr("ignore.section_room"))
                lines.extend(f"    [{kind}] {value}" for kind, value in room_entries)
        runtime_entries = store.entries(GLOBAL_SCOPE) if store is not None else []
        config_entries = [
            ("jid", entry) if "@" in entry else ("domain", entry)
            for entry in context.config.security.ignored_jids
        ]
        config_entries += [("nick", nick) for nick in context.config.security.ignored_nicks]
        if runtime_entries or config_entries:
            lines.append("  " + context.tr("ignore.section_global"))
            for kind, value in config_entries:
                lines.append(f"    [{kind}] {value} (config)")
            for kind, value in runtime_entries:
                lines.append(f"    [{kind}] {value}")
        if len(lines) == 1:
            lines.append("  " + context.tr("ignore.none"))
        return "\n".join(lines)

    # -- developer commands ----------------------------------------------

    def _require_dev(self, context: CommandContext, command: str) -> CommandResult | None:
        if not context.is_dev:
            return CommandResult(handled=True, reply=context.tr("dev.restricted", command=command))
        return None

    def _cmd_debug(self, args: list[str], context: CommandContext) -> CommandResult:
        denied = self._require_dev(context, "debug")
        if denied:
            return denied
        import json

        return CommandResult(handled=True, reply=json.dumps(context.config.redacted(), indent=2))

    def _cmd_context(self, args: list[str], context: CommandContext) -> CommandResult:
        denied = self._require_dev(context, "context")
        if denied:
            return denied
        estimate = context.session.estimator.count_messages(context.session.build_context())
        lines = [
            context.tr("context.tokens", estimate=estimate),
            context.tr("context.budget", budget=context.session.budget),
            context.tr("context.events", events=len(context.session.events)),
        ]
        return CommandResult(handled=True, reply="\n".join(lines))

    def _cmd_history(self, args: list[str], context: CommandContext) -> CommandResult:
        denied = self._require_dev(context, "history")
        if denied:
            return denied
        limit = 20
        if args:
            try:
                limit = max(1, min(100, int(args[0])))
            except ValueError:
                return CommandResult(handled=True, reply=context.tr("history.usage"))
        recent = context.session.events[-limit:]
        lines = [
            f"[{event.timestamp}] {event.type} "
            f"{(event.role or event.tool or '').strip()}: "
            f"{(event.content or '').splitlines()[0][:120] if event.content else ''}"
            for event in recent
        ]
        return CommandResult(handled=True, reply="\n".join(lines) or context.tr("history.empty"))

    def _cmd_tier(self, args: list[str], context: CommandContext) -> CommandResult:
        denied = self._require_dev(context, "tier")
        if denied:
            return denied
        tiers = self.config.llm.tiers
        available = [
            name for name in ("light", "standard", "heavy", "vision") if getattr(tiers, name)
        ]
        tier_list = ", ".join(available) if available else context.tr("tier.default_model")
        lines = [
            context.tr("tier.active", tier=context.tier),
            context.tr("tier.configured", tiers=tier_list),
        ]
        return CommandResult(handled=True, reply="\n".join(lines))

    def _cmd_usage(self, args: list[str], context: CommandContext) -> CommandResult:
        denied = self._require_dev(context, "usage")
        if denied:
            return denied
        total = context.usage_input + context.usage_output
        lines = [
            context.tr("usage.input", count=context.usage_input),
            context.tr("usage.output", count=context.usage_output),
            context.tr("usage.total", count=total),
        ]
        return CommandResult(handled=True, reply="\n".join(lines))

    def _cmd_raw(self, args: list[str], context: CommandContext) -> CommandResult:
        if not context.is_dev or not self.config.security.allow_raw_xml:
            return CommandResult(handled=True, reply=context.tr("raw.disabled"))
        return CommandResult(handled=True, reply=context.tr("raw.unavailable"))
