"""Sender authorization.

Security policy is enforced *before* any command or skill runs (SPEC §4, §65).
A JID is authorized when it matches the exact allowlist or its domain matches
the domain allowlist. ``"*"`` is only honoured when explicitly enabled.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.config.models import RoomConfig, SecurityConfig
from agent.security.ignore_store import IgnoreStore


def bare_jid(jid: str) -> str:
    """Return the lowercased bare JID (no resource)."""
    return jid.split("/", 1)[0].strip().lower()


def jid_domain(jid: str) -> str:
    """Return the lowercased domain part of a JID (after ``@`` if present)."""
    bare = bare_jid(jid)
    if "@" in bare:
        return bare.rsplit("@", 1)[1]
    return bare


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str


class Authorizer:
    """Evaluates the global and per-room allowlists."""

    def __init__(
        self,
        security: SecurityConfig,
        *,
        ignore_store: IgnoreStore | None = None,
    ) -> None:
        self.security = security
        self.ignore_store = ignore_store
        self._allowed_jids = {bare_jid(jid) for jid in security.allowed_jids}
        self._allowed_domains = {d.strip().lower() for d in security.allowed_domains}
        self._dev_jids = {bare_jid(jid) for jid in security.dev_command_jids}
        self._wildcard = security.allow_wildcard and "*" in self._allowed_domains

    def is_ignored_jid(self, jid: str) -> bool:
        """Whether the JID is globally ignored (runtime store).

        Config-level ignoring has been removed; only the runtime store applies.
        """
        return self.ignore_store is not None and self.ignore_store.is_ignored_jid(jid)

    def is_ignored_nick(self, room_jid: str, nick: str) -> bool:
        """Whether the nick is ignored in this room (per-room, runtime store)."""
        return self.ignore_store is not None and self.ignore_store.is_ignored_nick(room_jid, nick)

    def check(self, jid: str) -> Decision:
        """Decide whether *jid* may interact with the agent globally."""
        bare = bare_jid(jid)
        if not bare:
            return Decision(False, "empty JID")
        if self.is_ignored_jid(bare):
            return Decision(False, "sender is ignored")
        if bare in self._allowed_jids:
            return Decision(True, "explicit JID allowlist")
        if self._wildcard:
            return Decision(True, "wildcard domain")
        domain = jid_domain(jid)
        if domain in self._allowed_domains:
            return Decision(True, "domain allowlist")
        return Decision(False, "not in any allowlist")

    def is_authorized(self, jid: str) -> bool:
        return self.check(jid).allowed

    def is_dev(self, jid: str) -> bool:
        """Developer commands require an explicit JID entry (never a domain)."""
        return bare_jid(jid) in self._dev_jids

    def check_room(
        self,
        room: RoomConfig,
        jid: str,
        *,
        anonymous: bool = False,
        nick: str = "",
    ) -> Decision:
        """Decide whether *jid*/*nick* may invoke the agent inside a MUC room.

        Policy (SPEC §52):

        * A globally ignored JID is denied everywhere.
        * A nick ignored in *this* room is denied (public rooms hide the real JID).
        * An explicit ``room.allowed_jids`` is authoritative: the real JID must
          match. Anonymous occupants — whose real JID the server hides — cannot
          be verified and are therefore denied.
        * With ``room.restrict_to_allowed_jids = true`` the global policy is
          applied to the real JID; anonymous occupants are denied.
        * Otherwise the room itself is the trusted scope (it had to be joined
          explicitly by an administrator), so any occupant may talk to the bot.
        """
        if self.is_ignored_jid(bare_jid(jid)):
            return Decision(False, "sender is ignored")
        if nick and self.is_ignored_nick(room.jid, nick):
            return Decision(False, "nick is ignored in this room")

        if not room.allowed_jids and not room.restrict_to_allowed_jids:
            return Decision(True, "room is a trusted scope")

        if anonymous:
            return Decision(False, "anonymous occupant cannot be verified")

        if room.allowed_jids:
            allowed = {bare_jid(entry) for entry in room.allowed_jids}
            if bare_jid(jid) in allowed:
                return Decision(True, "room allowlist")
            return Decision(False, "not in room allowlist")
        return self.check(jid)
