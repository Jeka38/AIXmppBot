"""Structured logging setup.

Supports plain or JSON output. A filter guarantees secrets registered with the
redactor never reach log records even if a caller forgets to redact.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from agent.security.credentials import Redactor


class _RedactingFilter(logging.Filter):
    """Scrub registered secret values from a record before it is formatted.

    Redaction is applied to the *fully formatted* message and, only when a
    secret is actually present, the record is rewritten with ``args`` cleared.
    This never corrupts positional format specifiers such as ``%d`` used by
    third-party loggers (httpx, etc.).
    """

    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self.redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        if not self.redactor.has_secrets:
            return True
        try:
            formatted = record.getMessage()
        except Exception:  # noqa: BLE001 - never break logging because of a bad record
            return True
        redacted = self.redactor.redact(formatted)
        if redacted != formatted:
            record.msg = redacted
            record.args = ()
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("request_id", "jid", "session_id", "skill"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(
    level: str = "INFO",
    *,
    json_logs: bool = False,
    redactor: Redactor | None = None,
) -> None:
    """Configure the root logger once at startup."""
    root = logging.getLogger()
    root.setLevel(level.upper())
    for handler in list(root.handlers):
        root.removeHandler(handler)

    stream = logging.StreamHandler(sys.stderr)
    if json_logs:
        stream.setFormatter(_JsonFormatter())
    else:
        stream.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
    if redactor is not None:
        stream.addFilter(_RedactingFilter(redactor))
    root.addHandler(stream)

    # Slixmpp is extremely chatty at DEBUG.
    logging.getLogger("slixmpp").setLevel(max(logging.INFO, root.level))
    logging.getLogger("asyncio").setLevel(logging.WARNING)
