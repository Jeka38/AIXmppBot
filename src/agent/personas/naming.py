"""Persona name validation shared by the loader and the manager.

A persona name doubles as its directory name, so it must be a single safe path
segment. Names may use any Unicode letters and digits (Cyrillic, Greek, ...)
plus ``. _ -``, must start with a letter or digit, and are lowercased and
NFC-normalised for case-insensitive, locale-independent matching.
"""

from __future__ import annotations

import unicodedata

MAX_NAME_LENGTH = 64
_EXTRA_ALLOWED = frozenset({".", "_", "-"})


class InvalidPersonaName(ValueError):
    """Raised when a persona name is empty, too long or contains bad characters.

    ``key`` is an i18n message key and ``args`` its format arguments, so callers
    can render a localised message.
    """

    def __init__(self, key: str, **params: object) -> None:
        super().__init__(key)
        self.key = key
        self.params = params


def normalize_persona_name(raw: str) -> str:
    """NFC-normalise, trim and lowercase a persona name."""
    return unicodedata.normalize("NFC", raw.strip()).lower()


def validate_persona_name(raw: str) -> str:
    """Return a safe, normalised persona name or raise :class:`InvalidPersonaName`.

    Accepts Unicode letters/digits (so ``помощник`` is valid) plus ``. _ -``.
    Rejects path separators, control characters, leading punctuation and the
    bare ``.``/``..`` segments, so the result is always a single safe path
    component.
    """
    name = normalize_persona_name(raw)
    if not name:
        raise InvalidPersonaName("persona.name.empty")
    if len(name) > MAX_NAME_LENGTH:
        raise InvalidPersonaName("persona.name.too_long", max=MAX_NAME_LENGTH)
    if name in {".", ".."}:
        raise InvalidPersonaName("persona.name.invalid")
    if not name[0].isalnum():
        raise InvalidPersonaName("persona.name.start")
    for char in name:
        if char in _EXTRA_ALLOWED:
            continue
        # Unicode-aware: letters and digits from any script are accepted.
        if not char.isalnum():
            raise InvalidPersonaName("persona.name.invalid")
    return name
