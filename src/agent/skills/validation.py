"""Minimal JSON Schema argument validation.

We deliberately do not depend on a full JSON Schema implementation for the
foundation. This validator covers the subset skills actually use: ``type``,
``required``, ``properties``, ``enum``, ``minimum``/``maximum``, ``items``,
``minItems``/``maxItems`` and ``additionalProperties``. Unknown keywords are
ignored rather than rejected, so schemas remain portable to stricter validators
later.
"""

from __future__ import annotations

from typing import Any

from agent.errors import SkillError

_TYPE_MAP: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
    "null": type(None),
}


def validate_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> None:
    """Raise :class:`SkillError` if *arguments* do not satisfy *schema*."""
    if not isinstance(arguments, dict):
        raise SkillError("Arguments must be a JSON object")
    _validate(schema, arguments, path="$")


def _validate(schema: dict[str, Any], value: Any, *, path: str) -> None:
    expected = schema.get("type")
    if expected:
        allowed = _TYPE_MAP.get(expected)
        if allowed is not None and not _is_type(value, expected, allowed):
            raise SkillError(f"{path}: expected {expected}, got {type(value).__name__}")

    if "enum" in schema and value not in schema["enum"]:
        raise SkillError(f"{path}: value {value!r} is not one of {schema['enum']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise SkillError(f"{path}: must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise SkillError(f"{path}: must be <= {schema['maximum']}")

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for required in schema.get("required", []):
            if required not in value:
                raise SkillError(f"{path}: missing required property '{required}'")
        if schema.get("additionalProperties") is False:
            extras = set(value) - set(properties)
            if extras:
                raise SkillError(f"{path}: unexpected properties {sorted(extras)!r}")
        for key, subschema in properties.items():
            if key in value and isinstance(subschema, dict):
                _validate(subschema, value[key], path=f"{path}.{key}")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise SkillError(f"{path}: needs at least {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise SkillError(f"{path}: allows at most {schema['maxItems']} items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate(item_schema, item, path=f"{path}[{index}]")


def _is_type(value: Any, name: str, allowed: type | tuple[type, ...]) -> bool:
    if name in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, allowed)
