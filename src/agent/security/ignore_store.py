"""Persistent ignore-list added at runtime (``/ignore``).

Entries from the config (``security.ignored_jids`` / ``ignored_nicks``) are
static; this store holds ones an administrator adds in chat. Both are merged by
:class:`~agent.security.authorization.Authorizer`.

Stored as ``<state>/ignored.json``::

    {"jids": ["spammer@example.com"], "domains": ["evil.com"], "nicks": ["Flooder"]}
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

IgnoreKind = Literal["jid", "domain", "nick"]


@dataclass
class IgnoreStore:
    """Mutable, persistent ignore-list."""

    path: Path
    jids: set[str] = field(default_factory=set)
    domains: set[str] = field(default_factory=set)
    nicks: set[str] = field(default_factory=set)

    # -- persistence -----------------------------------------------------

    def load(self) -> IgnoreStore:
        if not self.path.is_file():
            return self
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            log.warning("Ignoring corrupt ignore store at %s", self.path)
            return self
        if not isinstance(raw, dict):
            return self
        self.jids = {str(v).strip().lower() for v in raw.get("jids", []) if str(v).strip()}
        self.domains = {str(v).strip().lower() for v in raw.get("domains", []) if str(v).strip()}
        self.nicks = {str(v).strip().lower() for v in raw.get("nicks", []) if str(v).strip()}
        return self

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "jids": sorted(self.jids),
            "domains": sorted(self.domains),
            "nicks": sorted(self.nicks),
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # -- mutation --------------------------------------------------------

    def add(self, kind: IgnoreKind, value: str) -> bool:
        """Add an entry; returns ``False`` if it was already present."""
        value = value.strip().lower()
        if not value:
            return False
        target = {"jid": self.jids, "domain": self.domains, "nick": self.nicks}[kind]
        if value in target:
            return False
        target.add(value)
        self._flush()
        return True

    def remove(self, kind: IgnoreKind, value: str) -> bool:
        value = value.strip().lower()
        target = {"jid": self.jids, "domain": self.domains, "nick": self.nicks}[kind]
        if value not in target:
            return False
        target.discard(value)
        self._flush()
        return True

    def entries(self) -> list[tuple[IgnoreKind, str]]:
        out: list[tuple[IgnoreKind, str]] = []
        out.extend(("jid", v) for v in sorted(self.jids))
        out.extend(("domain", v) for v in sorted(self.domains))
        out.extend(("nick", v) for v in sorted(self.nicks))
        return out


def classify_target(raw: str) -> tuple[IgnoreKind, str]:
    """Infer the kind of an ``/ignore`` target.

    ``nick:Name`` → nick, anything with ``@`` → JID, otherwise a domain.
    """
    value = raw.strip()
    if value.lower().startswith("nick:"):
        return "nick", value[5:].strip().lower()
    if "@" in value:
        return "jid", value.split("/", 1)[0].strip().lower()
    return "domain", value.lower()
