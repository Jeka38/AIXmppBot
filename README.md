# XMPP AI Agent

An XMPP-native AI agent written in Python, built on [Slixmpp](https://slixmpp.readthedocs.io/) 1.10.0.
Architecturally inspired by ProcessOne's Fluux Agent, but implemented natively in Python.

The agent connects to an XMPP server as a normal account (C2S) or as an XEP-0114
component, answers direct messages and group-chat mentions, runs an LLM agentic
loop with tools, and persists per-user / per-room history and memory.

> **Status: Phase 1–2 implemented.** See [Roadmap](#roadmap) for what is and is
> not implemented. The specification this project is built against lives in
> [`SPEC.md`](SPEC.md).

## Features

### Phase 1 — Foundation

- **C2S transport** with STARTTLS, SASL (PLAIN / SCRAM), resource binding and
  automatic reconnect with exponential backoff.
- **XEP-0114 component transport**, exposing the same runtime interface as C2S.
- **Direct messages** with per-JID session isolation.
- **MUC (XEP-0045)** join/rejoin, occupant tracking, `mention` and `all`
  activation modes, per-room isolation. A joined room is a trusted scope, so
  the bot works in public (semi-anonymous) rooms; set
  `restrict_to_allowed_jids = true` per room for a strict allowlist.
- **XEP-0085 chat states** (`composing` / `active` / `paused`).
- **XEP-0394 message markup**: the assistant's Markdown (`**bold**`, `*italic*`,
  `` `code` ``, `~~deleted~~`, fenced code blocks, lists, quotes) is sent as a
  plain `<body>` plus a `urn:xmpp:markup:0` element, so clients render
  formatting without XHTML-IM's spoofing/injection risks.
- **XEP-0071 XHTML-IM**: the same content is also sent as `<html/>`, because
  popular clients (Conversations, Monocles) render XHTML-IM but not XEP-0394.
  Clients that support neither still see clean plain text: tables are flattened,
  HTML tags and Markdown artifacts (`#`, `|`) are stripped, and all text is
  HTML-escaped.
- **Presence tracking** and a transport-agnostic presence event.
- **Agentic loop**: LLM → tool → result → LLM, with a bounded tool-round limit
  and error isolation (a failing skill becomes a tool result, never a crash).
- **LLM abstraction**: OpenAI-compatible, Anthropic, Ollama; `provider:model`
  tiering (`light` / `standard` / `heavy` / `vision`).
- **Security first**: JID / domain / room allowlists, capability checks,
  confirmation policy hooks, prompt-injection detection, credential redaction.
- **Memory**: `memory_store` / `memory_recall` skills with strict per-user
  isolation; secrets are refused.
- **Sessions**: JSONL transcripts per scope, token-budgeted context assembly.
- **Slash commands** intercepted before the LLM, with localised replies
  (`[agent] language = "en" | "ru"`; catalogs in `src/agent/i18n/locales/`).
- **CLI** with `--config`, `--check-config` and `--version`.

### Phase 3 — Advanced tools

- **Per-skill capabilities**: each skill runs with its own granted capabilities
  (declared by the skill or narrowed by `[skills.capabilities.<name>]`). Network
  access checks the target `host:port`; credential access checks the credential
  name — a skill cannot reach a host or read a secret it was not granted.
- **Confirmations**: destructive skills listed in `[security.confirmations]`
  require an explicit `yes`/`no` from the requesting user before running, with a
  TTL and per-scope isolation (DM and MUC).
- **Prompt-injection hardening** on skill output and credential redaction on all
  tool results.

### Phase 2 — Memory + Skills

- **Web search** (`web_search`) with Tavily and Perplexity providers; results are
  fenced as untrusted external data.
- **URL fetch** (`url_fetch`) with SSRF protection (private/loopback/link-local
  and cloud-metadata addresses blocked, redirects re-validated per hop), size
  limits and HTML→text extraction.
- **Attachments** (XEP-0066 OOB): images → vision data URLs, text/JSON/XML →
  text, PDF → text (via optional `pypdf`); temp files cleaned after each turn.
- **Personas**: directory-based definitions with per-user resolution
  (`user → user-persona → global persona → global`), `/persona` commands,
  create/delete, per-user isolation and configurable limits.
- **Context compaction**: when a transcript exceeds its token budget, the oldest
  events are folded into an LLM-generated summary that preserves facts,
  decisions, preferences, tool results and MUC authorship.
- **Long-term memory injection** into the system prompt.

## Architecture

XMPP is treated purely as transport. The runtime core has no Slixmpp imports.

```
XMPP server
   │  Slixmpp
   ▼
xmpp/            transport (C2S | component)
   │  MessageEvent / MUCMessageEvent / PresenceEvent
   ▼
runtime/         authorization → sessions → commands → agentic loop
   │
   ├─ security/  authorization, capabilities, injection, credentials
   ├─ llm/       router + OpenAI / Anthropic / Ollama clients
   ├─ skills/    registry + JSON Schema validation + built-ins
   └─ memory/    JSONL sessions, Markdown memory, context budgeting
```

See [`SPEC.md`](SPEC.md) §1.2 and §55 for the full target architecture.

## Requirements

- Python **3.12 – 3.13**. Slixmpp 1.10.0 ships a Rust extension that does not
  yet build on Python 3.14; use 3.13 or earlier.
- An XMPP account (or component registration) and an LLM API key (or a local
  Ollama server).

## Installation

```bash
python3.13 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Optional extras: `.[ollama]`, `.[mcp]`, `.[rag]`, `.[postgres]`.

## Configuration

Copy the example and fill in values. Secrets are referenced through
environment variables using `${VAR}` or `${VAR:-default}` expansion.

```bash
cp config/agent.toml.example config/agent.toml
```

```toml
[xmpp]
mode = "c2s"
jid = "${XMPP_JID}"
password = "${XMPP_PASSWORD}"
starttls = true
verify_tls = true

[security]
# Lists accept a TOML array or a comma-separated string from the environment.
allowed_jids = "${ALLOWED_JIDS:-}"
allowed_domains = "${ALLOWED_DOMAINS:-example.com}"
dev_command_jids = "${ADMIN_JIDS:-}"   # bot administrators

[llm]
provider = "openai"
base_url = "${LLM_BASE_URL:-https://api.openai.com/v1}"
api_key = "${LLM_API_KEY}"
model = "gpt-4o-mini"
```

A local Ollama setup uses the same file:

```toml
[llm]
provider = "ollama"
base_url = "http://127.0.0.1:11434"
model = "qwen3"
```

Validate without starting the agent:

```bash
XMPP_JID=... XMPP_PASSWORD=... LLM_API_KEY=... \
  python -m agent --config config/agent.toml --check-config
```

### Environment variables

| Variable | Purpose |
| --- | --- |
| `XMPP_JID`, `XMPP_PASSWORD` | C2S credentials |
| `XMPP_COMPONENT_SECRET` | Component secret |
| `ALLOWED_JIDS`, `ALLOWED_DOMAINS` | Authorization allowlists (comma-separated) |
| `ADMIN_JIDS` | Bot administrators who may run developer commands (comma-separated) |
| `LLM_API_KEY`, `LLM_BASE_URL` | LLM provider |
| `TAVILY_API_KEY` | Web search (Phase 2) |
| `GITHUB_TOKEN` | GitHub skill (Phase 3) |

## Running

```bash
python -m agent --config config/agent.toml
```

The agent prints a startup summary (never secrets) and connects. On disconnect,
Slixmpp reconnects with exponential backoff and the agent rejoins configured
rooms automatically.

## Slash commands

| Command | Effect |
| --- | --- |
| `/new`, `/reset` | Start a fresh session |
| `/forget` | Clear session and long-term memory |
| `/status` | Agent and session status |
| `/ping` | Connectivity check |
| `/help` | Command list |
| `/persona` | Show the active persona |
| `/persona list` | List available personas |
| `/persona <name>` | Switch persona |
| `/persona reset` | Revert to the default persona |
| `/persona create\|delete` | Manage personas (user in DM, room in MUC) |
| `/skills` | List enabled skills |
| `/ignore user@domain \| domain` | Admin (in DM): ignore a JID or domain everywhere |
| `/ignore Nick` (or `nick:Nick` / `@Nick`) | Room owner (inside the room): ignore a nick in this room |
| `/unignore <same>` | Remove an ignore entry |
| `/ignore` | Show the ignore list (room + global) |
| `/mode mention\|all` | Room owner: change the bot's activation mode (usable inside the room) |
| `/join <room> [nick] [mention\|all]` | Admin: join a public MUC room at runtime |
| `/leave [room]` | Admin: leave a room |
| `/rooms` | Admin: list active rooms |
| `/debug`, `/context`, `/history`, `/tier`, `/usage` | Developer commands (JIDs in `ADMIN_JIDS`) |

Commands are handled before the LLM and are never forwarded as prompts. Room
commands are available only to administrators (`ADMIN_JIDS`); joined rooms are
persisted under `data/state/rooms.json` and rejoined automatically after a
restart or reconnect.

### Language

Command replies and generated prompts are localised. Set the language in the
config:

```toml
[agent]
language = "ru"   # "en" (default) or "ru"
```

Translations live in separate catalogs under `src/agent/i18n/locales/`
(`en.json`, `ru.json`). Add a new locale by copying a catalog and setting its
BCP-47 primary tag as the filename (e.g. `de.json`); unknown keys fall back to
English, and an unknown locale falls back to `en`.

## Data layout

```
data/memory/
  users/<jid>/
    history.jsonl
    memory.md
    sessions/
  rooms/<room-jid>/
    history.jsonl
    memory.md
```

Sessions are JSONL; memory is Markdown. All access is confined to the configured
data directory, and each JID / room can only read its own scope.

Secrets are supplied through the environment (`.env`); they are never committed
to the repository.

## Security model

Security is enabled by default and enforced before anything executes:

1. **Authorization** — JID, domain and per-room allowlists. Commands and skills
   are never offered to unauthorized senders.
2. **Capabilities** — each skill declares `network`, `filesystem`, `credential`,
   `schedule`, `state`, `xmpp`, and the runtime verifies them before execution.
3. **Prompt injection** — external content (web, files, tool results) is fenced
   and flagged; it can never override the system policy.
4. **Credentials** — secrets are redacted from logs, history and tool results.
5. **Confirmation** — destructive skills can require explicit confirmation
   before running.

The LLM is treated as untrusted: it may *propose* actions, but the runtime
performs schema validation, authorization, capability checks and confirmation.

## Development

```bash
# tests
.venv/bin/python -m pytest
.venv/bin/python -m pytest tests/test_agentic_loop.py::test_single_tool_round  # single test

# lint / format / types
.venv/bin/ruff check src tests
.venv/bin/ruff format src tests
.venv/bin/mypy
```

Tests must cover, at minimum: authorization, session and MUC isolation, skill
permissions, prompt injection, credential redaction, SSRF, quotas, confirmation,
the agentic loop and tool-round limit, reconnect, JSONL recovery and persona
isolation (see [`SPEC.md`](SPEC.md) §57).

## Roadmap

| Phase | Scope | Status |
| --- | --- | --- |
| 1 | Foundation: C2S/component, reconnect, presence, MUC, XEP-0085, sessions, JSONL, commands, LLM clients, agentic loop, memory skills, security core | **Implemented** |
| 2 | Web search, URL fetch, attachments, vision, personas, context compaction/summarisation, long-term memory injection | **Implemented** |
| 3 | Per-skill capabilities, confirmations, prompt-injection hardening, REST skill engine | **In progress** (GitHub/JIRA/Front, quotas, generated skills deferred) |
| 4 | Scheduler, presence triggers, webhooks, PubSub, PEP, advanced MUC | Planned |
| 5 | Sub-agents, MCP, agent definitions, remote agent discovery/protocol | Planned |
| 6 | XEP-0198, XEP-0115, XEP-0280, PostgreSQL, distributed scheduler, production deployment | Planned |
| 7 | Wasm sandbox for generated skills | Planned |

## Personas

Personas are resolved per user (DM) **and per room** (MUC), each with its own
active persona and local persona definitions. Resolution order for each document
(first non-empty wins):

1. `<scope>/identity.md` (`data/memory/users/<jid>/` or `data/memory/rooms/<room>/`)
2. `<scope>/personas/<active>/identity.md`
3. `config/personas/<active>/identity.md` (and `data/personas/<active>/`)
4. a global document, if configured

A persona directory contains `persona.toml` plus optional `identity.md`,
`personality.md`, `instructions.md` and `context.md`. In a DM the `/persona`
command targets the user's personas; inside a room it targets the room's
personas. Both scopes are isolated from each other and limited by
`[personas] max_user_personas`.

Persona names may use any language (Latin, Cyrillic, Greek, ...): they accept
Unicode letters/digits plus `.`, `_` and `-`, are NFC-normalised and lowercased,
and cannot contain path separators.

### Creating personas from a prompt

Besides the `/persona create` command, the assistant can create personas itself:
ask it in natural language, e.g.

```
Create a persona called code-reviewer — a strict reviewer that flags insecure code.
```

The model writes the role documents and calls the `persona_create` tool, which
persists them in the caller's scope and activates the new persona. As with every
skill, the runtime validates the arguments and the write; the model never
touches the filesystem directly. `persona_create` is always offered to the model,
even under a persona whose `skills` list would otherwise hide it.

## License

MIT
