"""CLI entrypoint: ``python -m agent`` (SPEC §59)."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from agent import __version__
from agent.app import AgentApplication, startup_report
from agent.config.loader import validate_config
from agent.errors import AgentError, ConfigError
from agent.logging_setup import configure_logging

log = logging.getLogger(__name__)

DEFAULT_CONFIG = "config/agent.toml"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agent",
        description="XMPP-native AI agent",
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help=f"Path to the TOML configuration file (default: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Override the configured data directory",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Validate the configuration and exit",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"xmpp-ai-agent {__version__}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.check_config:
        try:
            config = validate_config(args.config)
        except ConfigError as exc:
            print(f"Configuration invalid: {exc}", file=sys.stderr)
            return 2
        print("Configuration OK")
        print()
        print(startup_report(config))
        return 0

    try:
        config = validate_config(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    # Deferred import so `--check-config` never requires Slixmpp installed.
    from agent.security.credentials import Redactor

    redactor = Redactor()
    redactor.register(config.llm.api_key, config.xmpp.password, config.xmpp.secret)
    configure_logging(config.logging.level, json_logs=config.logging.json_logs, redactor=redactor)

    print(startup_report(config))
    print()

    application = AgentApplication(config, data_dir=args.data_dir)
    try:
        asyncio.run(application.run_forever())
    except KeyboardInterrupt:
        log.info("Interrupted; shutting down")
    except AgentError as exc:
        log.error("Fatal agent error: %s", exc)
        return 1
    return 0


def _resolve_path(value: str) -> Path:
    return Path(value).expanduser()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
