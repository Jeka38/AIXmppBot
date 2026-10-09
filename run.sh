#!/usr/bin/env bash
# Launch the agent without Docker.
#
#   ./run.sh                 # uses config/agent.toml and .env
#   ./run.sh --check-config  # validate configuration and exit
#
# Reads secrets from .env (see .env.example). Requires the project venv.
set -euo pipefail
cd "$(dirname "$0")"

PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
    echo "venv not found; create it with: python3.13 -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
    exit 1
fi

if [ ! -f "config/agent.toml" ]; then
    echo "config/agent.toml missing; copy config/agent.toml.example and fill it in" >&2
    exit 1
fi

exec "$PY" -m agent --config config/agent.toml "$@"
