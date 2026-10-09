"""Logging redaction must not corrupt positional format specifiers."""

from __future__ import annotations

import io
import logging

from agent.logging_setup import _RedactingFilter, configure_logging
from agent.security.credentials import Redactor


def _record(msg: str, args: tuple) -> logging.LogRecord:
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )


def test_filter_preserves_percent_d_formatting() -> None:
    # httpx logs: 'HTTP Request: %s %s "%s %d %s"' with an int status code.
    redactor = Redactor()
    redactor.register("sk-supersecret-value")
    flt = _RedactingFilter(redactor)

    record = _record(
        'HTTP Request: %s %s "%s %d %s"',
        ("POST", "https://example.com", "HTTP/1.1", 200, "OK"),
    )
    assert flt.filter(record) is True
    # Must not raise and must render correctly.
    assert record.getMessage() == ('HTTP Request: POST https://example.com "HTTP/1.1 200 OK"')


def test_filter_redacts_secret_in_formatted_message() -> None:
    redactor = Redactor()
    redactor.register("sk-supersecret-value")
    flt = _RedactingFilter(redactor)

    record = _record("using key %s now", ("sk-supersecret-value",))
    flt.filter(record)
    assert "sk-supersecret-value" not in record.getMessage()
    assert "***" in record.getMessage()


def test_filter_noop_without_secrets() -> None:
    flt = _RedactingFilter(Redactor())
    record = _record("plain %d message", (7,))
    flt.filter(record)
    assert record.getMessage() == "plain 7 message"


def test_configure_logging_survives_percent_d() -> None:
    redactor = Redactor()
    redactor.register("sk-supersecret-value")
    buffer = io.StringIO()
    configure_logging("INFO", redactor=redactor)
    root = logging.getLogger()
    root.addHandler(logging.StreamHandler(buffer))

    # This must not emit a "Logging error".
    logging.getLogger("httpx").info(
        'HTTP Request: %s %s "%s %d %s"',
        "POST",
        "https://api.example.com",
        "HTTP/1.1",
        "200",
        "OK",
    )
    # Clean up handlers we added.
    root.handlers.clear()
