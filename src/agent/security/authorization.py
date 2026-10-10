"""Sender authorization.

Security policy is enforced *before* any command or skill runs (SPEC §4, §65).
A JID is authorized when it matches the exact allowlist or its domain matches
the domain allowlist. ``"*"`` is only honoured when explicitly enabled.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.config.models import RoomConfig, SecurityConfig
from agent.security.ignore_store import GLOBAL_SCOPE, IgnoreStore, scope_for_room


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
        # An "ignored_jids" entry with "@" is a JID; without it, a domain.
        self._ignored_jids: set[str] = set()
        self._ignored_domains: set[str] = set()
        for entry in security.ignored_jids:
            if "@" in entry:
                self._ignored_jids.add(bare_jid(entry))
            else:
                self._ignored_domains.add(entry)
        self._ignored_nicks = {nick.lower() for nick in security.ignored_nicks}

    def is_ignored(self, jid: str, *, nick: str = "", scope: str = GLOBAL_SCOPE) -> bool:
        """Whether *jid* (or MUC *nick*) must be silently ignored in *scope*.

        Ignoring takes priority over the allowlist: an ignored sender gets no
        reply, no command handling and no roster subscription. Config entries are
        always global; the runtime store is checked in *scope* and globally.
        """
        nick_lower = nick.lower()
        bare = bare_jid(jid)
        domain = jid_domain(jid)
        store = self.ignore_store
        if store is not None and store.matches(scope, jid=bare, nick=nick_lower):
            return True
        if nick_lower and nick_lower in self._ignored_nicks:
            return True
        if bare and bare in self._ignored_jids:
            return True
        if domain and domain in self._ignored_domains:
            return True
        return False

    def check(self, jid: str) -> Decision:
        """Decide whether *jid* may interact with the agent globally."""
        bare = bare_jid(jid)
        if not bare:
            return Decision(False, "empty JID")
        if self.is_ignored(jid):
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
        """Decide whether *jid* may invoke the agent inside a MUC room.

        Policy (SPEC §52):

        * An explicitly ignored sender is always denied (per-room or global).
        * An explicit ``room.allowed_jids`` is authoritative: the real JID must
          match. Anonymous occupants — whose real JID the server hides — cannot
          be verified and are therefore denied.
        * With ``room.restrict_to_allowed_jids = true`` the global policy is
          applied to the real JID; anonymous occupants are denied.
        * Otherwise the room itself is the trusted scope (it had to be joined
          explicitly by an administrator), so any occupant may talk to the bot.
        """
        if self.is_ignored(jid, nick=nick, scope=scope_for_room(room.jid)):
            return Decision(False, "sender is ignored")

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
