"""Lightweight internationalisation for user-facing text.

Translations live in separate per-locale JSON catalogs under ``locales/``
(``en.json``, ``ru.json``). A :class:`Translator` resolves a key, formats it
with ``str.format`` placeholders and falls back to English when a key or locale
is missing.

A process-wide default translator is configured at startup from
``agent.language``; classes may accept an explicit translator for isolation
(used in tests). Keys absent from every catalog are returned as-is so a
missing translation degrades to the key name instead of raising.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

DEFAULT_LOCALE = "en"
_LOCALES_DIR = Path(__file__).with_name("locales")


def supported_locales() -> list[str]:
    """Return the locales that have a catalog file."""
    return sorted(path.stem for path in _LOCALES_DIR.glob("*.json"))


def normalize_locale(value: str) -> str:
    """Map an arbitrary locale string to a supported catalog name.

    Accepts ``ru``, ``ru-RU``, ``ru_RU``; unknown locales fall back to English.
    """
    cleaned = (value or "").strip().lower().replace("_", "-")
    if not cleaned:
        return DEFAULT_LOCALE
    primary = cleaned.split("-", 1)[0]
    for candidate in (cleaned, primary):
        if (_LOCALES_DIR / f"{candidate}.json").is_file():
            return candidate
    return DEFAULT_LOCALE


@cache
def _catalog(locale: str) -> dict[str, str]:
    path = _LOCALES_DIR / f"{locale}.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(key): str(value) for key, value in data.items()}


class Translator:
    """Resolve and format localised strings."""

    def __init__(
        self,
        locale: str = DEFAULT_LOCALE,
        *,
        fallback: str = DEFAULT_LOCALE,
    ) -> None:
        self.locale = normalize_locale(locale)
        self.fallback = fallback
        self._primary = _catalog(self.locale)
        self._fallback = _catalog(fallback)

    def t(self, key: str, /, **kwargs: object) -> str:
        template = self._primary.get(key)
        if template is None:
            template = self._fallback.get(key)
        if template is None:
            return key
        if not kwargs:
            return template
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return template


_default = Translator()


def set_locale(locale: str) -> Translator:
    """Replace the process-wide default translator."""
    global _default
    _default = Translator(locale)
    return _default


def get_translator() -> Translator:
    return _default


def t(key: str, /, **kwargs: object) -> str:
    """Translate *key* using the process-wide default translator."""
    return _default.t(key, **kwargs)


__all__ = [
    "DEFAULT_LOCALE",
    "Translator",
    "get_translator",
    "normalize_locale",
    "set_locale",
    "supported_locales",
    "t",
]
