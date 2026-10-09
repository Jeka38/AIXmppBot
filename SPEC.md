# Задача

Разработай полноценного XMPP AI Agent на Python, архитектурно вдохновлённого Fluux Agent от ProcessOne, но реализованного нативно на Python.

Основной стек:

* Python 3.12+
* Slixmpp 1.10.0
* asyncio
* TOML-конфигурация
* JSONL для истории сессий
* Markdown для долгосрочной памяти и контекста
* HTTPX для HTTP/API
* Pydantic для схем данных и конфигурации
* SQLite для служебного persistent state
* Jinja2 для шаблонов
* optional: Ollama
* optional: MCP
* optional: PostgreSQL для production deployment
* Docker/Docker Compose для deployment

Не использовать Rust, Node.js или JavaScript runtime как обязательную часть системы.

Главная цель:

Создать XMPP-native AI Agent, который подключается к XMPP-серверу, общается через обычные XMPP-клиенты, работает в MUC, имеет память, LLM agentic loop, skills/tools, personas, web search, URL fetching, attachments, GitHub, MCP, планировщик, sub-agents и в дальнейшем возможность взаимодействия с другими агентами.

Проект должен быть production-oriented, модульным и расширяемым.

---

# 1. Основные принципы

## 1.1 Async-first

Всё приложение должно работать поверх asyncio.

Не использовать блокирующие операции внутри event loop.

Для блокирующих библиотек использовать:

```python
await asyncio.to_thread(...)
```

или отдельные async-compatible реализации.

---

## 1.2 XMPP является транспортом

XMPP не должен быть смешан с бизнес-логикой.

Архитектура:

```text
                 ┌──────────────────────┐
                 │      XMPP Server     │
                 └──────────┬───────────┘
                            │
                     XMPP / Slixmpp
                            │
                 ┌──────────▼───────────┐
                 │     XMPP Gateway     │
                 │                     │
                 │ messages             │
                 │ presence             │
                 │ MUC                 │
                 │ IQ                  │
                 │ attachments         │
                 └──────────┬───────────┘
                            │
                 ┌──────────▼───────────┐
                 │    Agent Runtime     │
                 │                     │
                 │ sessions             │
                 │ context             │
                 │ personas             │
                 │ commands             │
                 │ security             │
                 └──────────┬───────────┘
                            │
                 ┌──────────▼───────────┐
                 │     Agent Loop       │
                 │                     │
                 │ LLM → tool → result │
                 │       ↓             │
                 │      LLM            │
                 └──────────┬───────────┘
                            │
            ┌───────────────┼────────────────┐
            │               │                │
        Skills           Memory           Agents
            │               │                │
       Web/GitHub        RAG/JSONL       Sub-agents
       URL/JIRA          context         MCP
       REST              sessions        Scheduler
```

XMPP transport должен быть заменяемым.

Agent Runtime не должен напрямую зависеть от Slixmpp.

---

# 2. Поддержка XMPP

Использовать Slixmpp 1.10.0.

Поддержать:

## C2S

Подключение обычного XMPP аккаунта.

Поддержать:

* SASL PLAIN
* SASL SCRAM-SHA-1
* STARTTLS
* TLS
* reconnect
* exponential backoff
* presence
* roster
* direct messages

Конфигурация:

```toml
[xmpp]
mode = "c2s"

jid = "${XMPP_JID}"
password = "${XMPP_PASSWORD}"

host = "xmpp.example.com"
port = 5222

starttls = true
verify_tls = true

resource = "ai-agent"
```

---

# 3. Component mode

Поддержать XEP-0114.

Пример:

```toml
[xmpp]
mode = "component"

host = "127.0.0.1"
port = 5347

component_jid = "agent.example.com"
secret = "${XMPP_COMPONENT_SECRET}"
```

Agent Runtime не должен знать, работает ли XMPP через C2S или component.

Оба режима должны предоставлять одинаковый интерфейс:

```python
class XMPPTransport(Protocol):
    async def send_message(...)
    async def send_presence(...)
    async def join_muc(...)
    async def leave_muc(...)
```

---

# 4. Безопасность XMPP

Безопасность должна быть включена по умолчанию.

Добавить:

```toml
[security]

allowed_jids = [
    "alice@example.com",
    "bob@example.com"
]

allowed_domains = [
    "example.com"
]
```

Проверять:

1. domain allowlist
2. JID allowlist
3. room allowlist
4. skill permissions
5. persona permissions
6. credential permissions

Никогда не принимать команды от неавторизованных JID.

Не выполнять skills до прохождения authorization.

Для production:

```toml
allowed_domains = ["example.com"]
```

является default.

`"*"` разрешается только при явном включении.

---

# 5. XEP-0085 Chat States

Полностью поддержать XEP-0085.

При начале обработки сообщения:

```xml
<composing/>
```

При успешном ответе:

```xml
<active/>
```

При ошибке:

```xml
<paused/>
```

Slash commands не должны отправлять composing.

---

# 6. Presence

В C2S режиме:

* subscribe allowed JIDs
* auto-accept allowed JIDs
* track available/unavailable
* хранить last presence
* поддерживать deferred tasks

Система должна иметь события:

```python
PresenceEvent(
    jid=...,
    available=True,
    status=...,
    show=...,
)
```

---

# 7. MUC / XEP-0045

Поддержать полноценный MUC.

Конфигурация:

```toml
[[rooms]]

jid = "room@conference.example.com"
nick = "AI-Agent"

activation = "mention"

history_token_budget = 80000
```

Режимы:

```text
mention
all
```

В `mention`:

```text
Alice: привет
Bob: @agent расскажи про XMPP
Agent: ...
```

Агент отвечает только если его упомянули.

В `all` отвечает на каждое сообщение.

Поддержать:

* join
* leave
* reconnect
* rejoin
* room presence
* occupant tracking
* nick
* mentions
* room-specific context
* room-specific instructions
* room-specific memory
* invitations
* kicks
* unavailable/rejoin
* participant awareness

Room context должен сохранять:

```text
[from=alice@example.com]
...

[from=bob@example.com]
...
```

При summarization обязательно сохранять автора каждого важного сообщения.

---

# 8. Agentic loop

Главное ядро системы — agentic loop.

Пример:

```text
User
 ↓
LLM
 ↓
tool_call
 ↓
Skill
 ↓
tool_result
 ↓
LLM
 ↓
tool_call
 ↓
Skill
 ↓
LLM
 ↓
final answer
```

Не ограничивать LLM одним tool call.

Конфигурация:

```toml
[agent]

max_tool_rounds = 10
```

Если достигнут лимит:

1. прекратить выполнение tools
2. запросить финальный ответ
3. сообщить пользователю об ограничении, если необходимо

Ошибки skills не должны уничтожать всю сессию.

Они передаются обратно в LLM как `tool_result`.

---

# 9. LLM abstraction

Не привязывать runtime к конкретному провайдеру.

Создать:

```python
class LLMClient(Protocol):

    async def chat(...):
        ...

    async def stream(...):
        ...
```

Поддержать минимум:

```text
OpenAI-compatible API
Anthropic
Ollama
```

OpenAI-compatible endpoint должен позволять использовать:

* OpenAI
* OpenRouter
* RouterAI
* локальные OpenAI-compatible серверы
* другие совместимые API

Конфигурация:

```toml
[llm]

provider = "openai"

base_url = "${LLM_BASE_URL}"
api_key = "${LLM_API_KEY}"

model = "..."

max_tokens = 8192
temperature = 0.7
```

---

# 10. Ollama

Поддержать локальные модели:

```toml
[llm]

provider = "ollama"
base_url = "http://127.0.0.1:11434"
model = "qwen3"
```

Не требовать API key.

Поддержать tool calling, если модель его поддерживает.

---

# 11. Model tiering

Реализовать уровни:

```text
light
standard
heavy
vision
```

Пример:

```toml
[llm.tiers]

light = "openai:gpt-5.6-mini"
standard = "openai:gpt-5.6"
heavy = "openai:gpt-5.6"
vision = "openai:gpt-5.6"
```

LLM runtime может выбирать tier в зависимости от задачи.

Примеры:

```text
translation → light
classification → light
normal conversation → standard
complex research → heavy
image analysis → vision
```

---

# 12. Agent profiles / Personas

Реализовать personas.

Структура:

```text
data/personas/
├── general-assistant/
│   ├── persona.toml
│   ├── identity.md
│   ├── personality.md
│   ├── instructions.md
│   └── context.md
│
├── coding-mentor/
├── code-reviewer/
├── support-agent/
└── creative-writer/
```

`persona.toml`:

```toml
[persona]

id = "coding-mentor"
name = "Coding Mentor"
description = "Programming mentor"

[persona.defaults]

tier = "standard"

skills = [
    "web_search",
    "url_fetch"
]

max_tokens_per_message = 8192
```

---

# 13. Persona resolution

При создании system prompt использовать:

```text
1. user/{jid}/identity.md
2. user/{jid}/personas/{active}/identity.md
3. personas/{active}/identity.md
4. global identity.md
```

Аналогично:

```text
personality.md
instructions.md
context.md
```

Пользователь может иметь собственную persona.

---

# 14. Persona commands

Реализовать:

```text
/persona
/persona list
/persona <name>
/persona reset
```

Дополнительно:

```text
/persona create <name>
/persona delete <name>
/persona export <name>
/persona import
```

Создание пользовательских personas должно быть отключаемым:

```toml
[personas]

allow_user_personas = true
max_user_personas = 5
allow_sharing = true
```

---

# 15. Sessions

Каждый пользователь получает независимую session.

Структура:

```text
data/

memory/
    users/
        alice@example.com/
            history.jsonl
            memory.md
            user.md
            active_persona.txt
            sessions/
                20261008-120000.jsonl
                20261008-150000.jsonl
            personas/
                study-buddy/
                    persona.toml
                    identity.md
                    personality.md
                    instructions.md
```

Для MUC:

```text
memory/
    rooms/
        room@conference.example.com/
            history.jsonl
            memory.md
```

---

# 16. JSONL session format

Каждое событие должно быть структурировано.

Например:

```json
{
  "type": "message",
  "role": "user",
  "sender": "alice@example.com",
  "content": "Привет",
  "timestamp": "2026-10-08T12:00:00Z",
  "message_id": "..."
}
```

Tool:

```json
{
  "type": "tool_call",
  "tool": "web_search",
  "arguments": {},
  "timestamp": "..."
}
```

Tool result:

```json
{
  "type": "tool_result",
  "tool": "web_search",
  "content": "...",
  "timestamp": "..."
}
```

Summary:

```json
{
  "type": "summary",
  "compacted_count": 45,
  "content": "...",
  "timestamp": "..."
}
```

---

# 17. Slash commands

Slash commands перехватываются до LLM.

Они никогда не должны попадать в LLM как обычное пользовательское сообщение.

Минимум:

```text
/new
/reset
/forget
/status
/help
/ping
```

Developer commands:

```text
/debug
/context
/history
/tier
/usage
```

`/raw` разрешить только admin JID и только если явно включено.

```toml
[agent]

dev_command_jids = [
    "admin@example.com"
]

allow_raw_xml = false
```

---

# 18. Skills system

Создать универсальный интерфейс:

```python
class Skill(Protocol):

    @property
    def name(self) -> str:
        ...

    @property
    def description(self) -> str:
        ...

    def parameters_schema(self) -> dict:
        ...

    def capabilities(self) -> list[str]:
        ...

    async def execute(self, arguments: dict, context: SkillContext):
        ...
```

Registry:

```python
class SkillRegistry:
    register(...)
    unregister(...)
    get(...)
    list(...)
    tool_definitions(...)
```

Каждый skill обязан иметь JSON Schema.

---

# 19. Built-in skills

Реализовать:

```text
web_search
url_fetch
memory_store
memory_recall
```

Затем:

```text
github
jira
front
```

---

# 20. Web search

Создать abstraction:

```python
class SearchProvider(Protocol):
    async def search(...)
```

Поддержать:

```text
Tavily
Perplexity
```

Конфигурация:

```toml
[skills.web_search]

enabled = true

provider = "tavily"

api_key = "${TAVILY_API_KEY}"

max_results = 5
```

LLM должен получать результаты поиска структурированно.

---

# 21. URL fetch

Skill:

```text
url_fetch
```

Должен:

1. скачать URL;
2. проверить redirect;
3. ограничить размер;
4. определить content type;
5. извлечь текст;
6. передать содержимое LLM;
7. сделать summary при необходимости.

Обязательно защищаться от SSRF.

Запрещать:

```text
127.0.0.0/8
10.0.0.0/8
172.16.0.0/12
192.168.0.0/16
169.254.169.254
::1
fc00::/7
```

если это не разрешено отдельной policy.

---

# 22. Memory

Разделить:

```text
session memory
long-term memory
user profile
shared knowledge
```

Skills:

```text
memory_store
memory_recall
```

Пример:

```text
User: Запомни, что я предпочитаю Python.

Agent:
Сохранено.
```

Хранить:

```text
memory.md
user.md
```

Не сохранять секреты, пароли и API keys в memory.

---

# 23. Proactive context learning

Агент должен уметь автоматически извлекать важную информацию из диалогов.

Например:

```text
User prefers Python.
User works on XMPP client.
Project uses Slixmpp 1.10.0.
```

Но extraction должен иметь отдельный этап.

LLM не должен самостоятельно менять memory filesystem обычным текстовым ответом.

Только через контролируемый memory skill.

---

# 24. Context window management

Не передавать всю историю бесконечно.

Ввести:

```toml
[llm]

dm_history_token_budget = 50000
muc_history_token_budget = 80000
```

Алгоритм:

```text
history
 ↓
token estimation
 ↓
within budget?
 ├─ yes → send
 └─ no
      ↓
memory flush
      ↓
summarization
      ↓
compaction
      ↓
send compacted history
```

При compaction сохранять:

* факты;
* решения;
* user preferences;
* project state;
* важные tool results;
* участников MUC;
* автора каждого важного сообщения.

---

# 25. Attachments

Поддержать XMPP attachments через XEP-0066 / OOB.

При получении:

```text
image
pdf
document
text file
```

Агент должен:

1. определить URL;
2. проверить безопасность;
3. скачать;
4. проверить размер;
5. определить MIME;
6. сохранить во временное хранилище;
7. передать LLM;
8. удалить временный файл после обработки.

Не скачивать произвольные внутренние адреса.

Конфигурация:

```toml
[attachments]

enabled = true
max_size_mb = 25
download_timeout = 30
```

---

# 26. Vision

Если LLM поддерживает vision:

```text
User → image → XMPP → attachment handler → vision LLM
```

Для моделей без vision:

```text
image → OCR / description → text → LLM
```

---

# 27. GitHub skill

Реализовать:

```text
github_list_issues
github_get_issue
github_create_issue
github_comment_issue
github_list_prs
github_get_pr
github_list_repos
github_search
github_notifications
```

Конфигурация:

```toml
[skills.github]

enabled = true

token = "${GITHUB_TOKEN}"

default_owner = "..."
```

Примеры:

```text
Покажи открытые bug issues.

Найди PR #42.

Создай issue для исправления авторизации.

Найди все issue с "memory leak".
```

---

# 28. REST skill engine

Создать declarative REST skill format.

Пример:

```toml
[skill]

type = "rest_api"

id = "front_conversations"

name = "Front Conversations"

description = "List open Front conversations"

[skill.capabilities]

network = [
    "api2.frontapp.com:443"
]

credential = [
    "front-api-key"
]

[skill.request]

method = "GET"

url = "https://api2.frontapp.com/conversations"

query = {
    limit = "100"
}

headers = {
    Authorization = "Bearer ${FRONT_API_KEY}"
}

[skill.response]

format = "json"

extract = "..."
```

На v0.2:

НИКАКОГО arbitrary code execution.

REST skill может:

* делать HTTP request;
* использовать объявленные credentials;
* использовать объявленные hosts;
* безопасно преобразовывать JSON.

---

# 29. Agent-generated skills

Если пользователь просит действие, для которого нет skill:

```text
User:
Проверь мой Front inbox.

Agent:
У меня нет Front skill.
Я могу создать declarative REST skill.
```

Агент создаёт skill definition.

Но:

* не выполняет shell;
* не создаёт Python;
* не запускает произвольный код;
* не получает root;
* не имеет доступа к filesystem вне разрешённого каталога.

Для сложных skills:

```text
draft → validate → human approval → enable
```

---

# 30. Skill commands

Реализовать:

```text
/skills
/skill info <name>
/skill disable <name>
/skill enable <name>
/skill delete <name>
```

---

# 31. Capabilities

Каждый skill объявляет capabilities:

```text
network
filesystem
credential
schedule
state
xmpp
```

Например:

```toml
[skill.capabilities]

network = ["api.github.com:443"]
credential = ["github-token"]
```

Runtime обязан проверять capability перед выполнением.

---

# 32. Prompt injection protection

Добавить отдельный security layer.

Проверять:

* входящие сообщения;
* web pages;
* documents;
* attachments;
* tool results.

Никогда не считать содержимое web page/system-like instructions доверенными инструкциями.

Принцип:

```text
System policy
    >
Developer policy
    >
Runtime security policy
    >
User request
    >
External content
```

Текст сайта:

```text
Ignore previous instructions...
```

не должен менять system policy.

---

# 33. Credential management

Секреты:

```text
API keys
OAuth tokens
passwords
XMPP credentials
```

не должны попадать:

* в LLM prompt;
* в history;
* в logs;
* в memory;
* в tool result.

Поддержать:

```text
environment variables
.env
encrypted credential store
```

---

# 34. MCP

Реализовать MCP bridge.

Архитектура:

```text
MCP Server
    ↓
MCP Client
    ↓
Skill Adapter
    ↓
SkillRegistry
    ↓
Agent
```

Каждый MCP tool должен становиться skill.

Пример:

```text
filesystem
github
database
custom API
```

MCP server должен запускаться отдельно.

Не смешивать MCP transport с Agent Runtime.

---

# 35. Sub-agents

Добавить runtime tool:

```text
spawn_agent
```

Конфигурация:

```toml
[llm.agents.researcher]

model = "..."
persona = "researcher"

skills = [
    "web_search",
    "url_fetch"
]

max_tokens = 8192
```

Другой:

```toml
[llm.agents.summarizer]

model = "..."
persona = "summarizer"

skills = []
```

Sub-agent должен иметь:

* отдельный context;
* отдельный prompt;
* отдельный model;
* отдельные skills;
* отдельный token budget.

На первом этапе:

```text
max depth = 1
```

Запретить бесконечное порождение агентов.

---

# 36. Scheduled tasks

Создать scheduler.

Поддержать:

```text
cron
interval
one-shot
```

Примеры:

```text
Каждое утро присылай новости.

Проверяй GitHub каждые 30 минут.

Напомни через 2 часа.
```

Конфигурация:

```toml
[[tasks]]

id = "morning_news"

schedule = "0 9 * * *"

jid = "alice@example.com"

prompt = "Сделай краткую сводку новостей"
```

---

# 37. Webhooks

Добавить HTTP endpoint:

```text
POST /webhook/<id>
```

Webhook должен:

```text
HTTP
 ↓
validate secret/signature
 ↓
event
 ↓
agent task
 ↓
skill/LLM
 ↓
XMPP notification
```

Обязательно:

* authentication;
* rate limit;
* payload size limit;
* audit log.

---

# 38. PubSub

Поддержать XMPP PubSub как источник событий.

Архитектура:

```text
PubSub
 ↓
event bus
 ↓
agent
 ↓
skill
 ↓
XMPP
```

---

# 39. Presence-based proactivity

Агент может реагировать на:

```text
user online
user offline
```

Например:

```text
User comes online
        ↓
pending tasks?
        ↓
yes
        ↓
send summary
```

Настройка должна быть отключаемой.

---

# 40. PEP

Поддержать XEP-0163 как optional feature.

Реагировать на:

* mood;
* activity;
* tune;
* avatar;
* location.

Но:

* только allowed JID;
* opt-in;
* не отправлять PEP data третьим сторонам;
* не хранить без необходимости.

---

# 41. XEP-0198 Stream Management

Поддержать XEP-0198 для повышения надёжности.

Нужно:

* stanza acknowledgements;
* session resumption;
* reconnect;
* resend;
* duplicate protection.

Особенно важно при нестабильном соединении.

---

# 42. XEP-0115 Entity Capabilities

В C2S режиме публиковать capabilities агента.

Disco должен сообщать доступные функции.

---

# 43. XEP-0280 Message Carbons

Поддержать Message Carbons для синхронизации сообщений между ресурсами агента.

Не допускать:

```text
carbon → agent → carbon → loop
```

---

# 44. Agent discovery

Подготовить архитектуру под agent-to-agent federation.

В дальнейшем агент должен уметь:

```text
disco#info
    ↓
agent capabilities
    ↓
discover remote agent
    ↓
delegate task
```

Предусмотреть собственный namespace:

```text
urn:fluux:agent:0
```

Но не реализовывать federation до отдельного этапа.

---

# 45. Agent-to-agent protocol

Архитектура должна быть готова к:

```text
Agent A
   ↓
discover Agent B
   ↓
capabilities
   ↓
execute task
   ↓
result
```

Будущие операции:

```text
discover
execute
confirm
```

Аутентификация и authorization обязательны.

---

# 46. Storage abstraction

Не привязывать всё к filesystem.

Создать:

```python
class SessionStore(Protocol):
    ...

class MemoryStore(Protocol):
    ...

class CredentialStore(Protocol):
    ...

class TaskStore(Protocol):
    ...
```

Первая реализация:

```text
JSONL + Markdown + SQLite
```

Будущая:

```text
PostgreSQL
```

---

# 47. Configuration

Использовать TOML.

Главный файл:

```text
config/agent.toml
```

Environment expansion:

```toml
api_key = "${OPENAI_API_KEY}"
```

Поддержать default values.

Не хранить секреты непосредственно в git.

---

# 48. Logging

Использовать structured logging.

Уровни:

```text
DEBUG
INFO
WARNING
ERROR
```

Каждый запрос желательно иметь:

```text
request_id
jid
session_id
skill
```

Никогда не логировать:

* passwords;
* API keys;
* tokens;
* credentials;
* private attachment contents.

---

# 49. Cost tracking

Отслеживать:

```text
input tokens
output tokens
tool calls
model
user
session
```

Хранить:

```text
usage
```

Команда:

```text
/usage
```

Конфигурация:

```toml
[quotas]

enabled = true

daily_tokens = 100000

per_user_daily_tokens = 20000
```

При превышении:

```text
reject
```

или:

```text
fallback to light model
```

---

# 50. Action validation

Для опасных действий отделить:

```text
LLM decision
```

от:

```text
runtime authorization
```

Пример:

```text
LLM:
create GitHub issue

Runtime:
user allowed?
skill allowed?
capability allowed?
quota?
confirmation required?
```

Только после этого:

```text
execute
```

---

# 51. Human confirmation

Для destructive actions:

```text
delete
send
publish
create
modify
```

поддержать confirmation policy.

Например:

```toml
[security.confirmations]

github_create_issue = false
github_delete = true
send_email = true
```

В XMPP:

```text
Agent:
Я собираюсь удалить issue #123.
Подтвердить? [yes/no]
```

Не выполнять до подтверждения.

---

# 52. MUC security

Для каждого room:

```toml
[[rooms]]

jid = "dev@conference.example.com"

allowed_jids = [
    "alice@example.com",
    "bob@example.com"
]

activation = "mention"
```

Не считать сообщения других участников автоматически доверенными инструкциями.

---

# 53. Error handling

Ошибки должны быть классифицированы:

```text
XMPPError
LLMError
SkillError
AuthenticationError
AuthorizationError
TimeoutError
StorageError
SecurityError
QuotaError
```

Пользователю показывать понятное сообщение.

В logs — подробный traceback.

---

# 54. Reconnection

XMPP reconnect:

```text
1 sec
2 sec
4 sec
8 sec
...
max 5 min
```

После reconnect:

1. restore session;
2. restore presence;
3. rejoin MUC;
4. restore subscriptions;
5. restore XEP-0198 state if possible.

---

# 55. Project structure

Использовать примерно:

```text
fluux_agent_python/

├── pyproject.toml
├── README.md
├── LICENSE
├── Dockerfile
├── docker-compose.yml
├── .env.example
│
├── config/
│   ├── agent.toml.example
│   └── personas/
│
├── data/
│   ├── memory/
│   ├── personas/
│   ├── skills/
│   └── state/
│
├── src/
│   └── agent/
│       ├── __init__.py
│       ├── main.py
│       │
│       ├── config/
│       │   ├── loader.py
│       │   └── models.py
│       │
│       ├── xmpp/
│       │   ├── client.py
│       │   ├── component.py
│       │   ├── events.py
│       │   ├── muc.py
│       │   ├── presence.py
│       │   ├── attachments.py
│       │   └── security.py
│       │
│       ├── runtime/
│       │   ├── runtime.py
│       │   ├── sessions.py
│       │   ├── commands.py
│       │   ├── context.py
│       │   └── security.py
│       │
│       ├── llm/
│       │   ├── base.py
│       │   ├── openai.py
│       │   ├── anthropic.py
│       │   ├── ollama.py
│       │   ├── router.py
│       │   └── models.py
│       │
│       ├── skills/
│       │   ├── base.py
│       │   ├── registry.py
│       │   ├── context.py
│       │   ├── web_search.py
│       │   ├── url_fetch.py
│       │   ├── memory.py
│       │   ├── github.py
│       │   ├── rest.py
│       │   └── generated.py
│       │
│       ├── personas/
│       │   ├── manager.py
│       │   └── loader.py
│       │
│       ├── memory/
│       │   ├── store.py
│       │   ├── session.py
│       │   ├── compaction.py
│       │   └── rag.py
│       │
│       ├── agents/
│       │   ├── manager.py
│       │   ├── subagent.py
│       │   └── definitions.py
│       │
│       ├── scheduler/
│       │   ├── scheduler.py
│       │   └── tasks.py
│       │
│       ├── mcp/
│       │   ├── client.py
│       │   └── adapter.py
│       │
│       ├── security/
│       │   ├── authorization.py
│       │   ├── capabilities.py
│       │   ├── injection.py
│       │   ├── credentials.py
│       │   └── confirmation.py
│       │
│       └── api/
│           └── webhooks.py
│
└── tests/
    ├── test_xmpp.py
    ├── test_sessions.py
    ├── test_skills.py
    ├── test_security.py
    ├── test_personas.py
    ├── test_muc.py
    └── test_agentic_loop.py
```

---

# 56. Development phases

Не пытайся реализовать всё одной огромной итерацией.

Работай строго по фазам.

## Phase 1 — Foundation

Реализовать:

* project structure;
* configuration;
* C2S;
* STARTTLS;
* authentication;
* reconnect;
* presence;
* allowed JIDs;
* direct messages;
* XEP-0085;
* slash commands;
* sessions;
* JSONL;
* basic LLM client;
* agentic runtime;
* basic MUC.

Результат:

```text
XMPP client
    ↓
LLM
    ↓
answer
```

---

## Phase 2 — Memory + Skills

Добавить:

* memory;
* memory_store;
* memory_recall;
* web_search;
* url_fetch;
* attachments;
* Ollama;
* model abstraction;
* personas;
* token budgeting;
* compaction.

---

## Phase 3 — Advanced tools

Добавить:

* GitHub;
* REST skill;
* JIRA;
* Front;
* generated skills;
* capability system;
* prompt injection detection;
* cost tracking;
* quotas;
* confirmations.

---

## Phase 4 — Proactivity

Добавить:

* scheduler;
* cron;
* presence triggers;
* deferred tasks;
* webhooks;
* PubSub;
* PEP;
* advanced MUC.

---

## Phase 5 — Agent architecture

Добавить:

* sub-agents;
* model tiering;
* MCP;
* agent definitions;
* remote agent discovery;
* agent protocol.

---

## Phase 6 — Reliability

Добавить:

* XEP-0198;
* XEP-0115;
* XEP-0280;
* stronger persistence;
* PostgreSQL;
* distributed scheduler;
* production Docker deployment.

---

## Phase 7 — Sandbox

Не выполнять arbitrary generated Python.

Для будущего sandbox:

```text
Wasm
+
Wasmtime
+
capability restrictions
```

До реализации sandbox generated skills должны быть declarative.

---

# 57. Tests

Обязательно писать tests одновременно с функциональностью.

Минимум:

```text
authorization
session isolation
MUC isolation
skill permissions
prompt injection
credential redaction
URL SSRF
quota enforcement
confirmation flow
agentic loop
tool round limit
reconnect
JSONL recovery
persona isolation
```

Особенно проверить:

```text
Alice cannot access Bob memory.
Bob cannot access Alice memory.
MUC A cannot access MUC B memory.
Unauthorized JID cannot invoke agent.
LLM cannot bypass capability restrictions.
LLM cannot directly execute shell.
External webpage cannot override system prompt.
```

---

# 58. Docker

Создать production Dockerfile.

Запуск:

```bash
docker compose up -d
```

Environment:

```text
XMPP_JID
XMPP_PASSWORD
LLM_API_KEY
GITHUB_TOKEN
TAVILY_API_KEY
```

Не помещать секреты в image.

---

# 59. CLI

Создать CLI:

```bash
python -m agent
```

Поддержать:

```bash
python -m agent --config config/agent.toml
python -m agent --check-config
python -m agent --version
```

---

# 60. Observability

При startup:

```text
Fluux Python Agent
==================

XMPP mode: c2s
JID: agent@example.com
LLM: openai:gpt-...
Skills: 8
Personas: 4
MUC rooms: 2
Memory: enabled
Scheduler: enabled
MCP: enabled
```

Не показывать секреты.

---

# 61. README

README должен содержать:

1. Что это.
2. Архитектуру.
3. Установку.
4. XMPP setup.
5. LLM setup.
6. Configuration.
7. Skills.
8. Personas.
9. MUC.
10. Memory.
11. Docker.
12. Security.
13. Development.
14. Tests.
15. Roadmap.

---

# 62. Кодовые требования

Пиши production-quality код.

Не использовать:

```python
# TODO
# implement later
pass
```

для функциональности, которая заявлена как реализованная.

Не оставлять mock implementation вместо настоящей.

Если функциональность ещё не реализована — явно оформить её как отдельный следующий этап.

Каждый модуль должен иметь чёткую ответственность.

Не создавать гигантский `main.py`.

---

# 63. Типизация

Использовать:

```python
from typing import Protocol
```

где нужны interfaces.

Использовать:

```python
@dataclass
```

или Pydantic models для структур данных.

Добавить type hints во весь production code.

Цель:

```bash
mypy
```

без критических ошибок.

---

# 64. Dependency policy

Минимум зависимостей.

Основные:

```text
slixmpp==1.10.0
httpx
pydantic
pydantic-settings
tomli-w
jinja2
tiktoken / tokenizer abstraction
```

Не добавлять библиотеку без необходимости.

Для optional functionality использовать extras:

```text
pip install .[ollama]
pip install .[mcp]
pip install .[rag]
pip install .[postgres]
```

---

# 65. Главное правило архитектуры

LLM НЕ является доверенным компонентом.

LLM может:

```text
предлагать действие
```

но не может самостоятельно:

```text
получать credentials
обходить permissions
вызывать shell
читать arbitrary filesystem
отправлять произвольные XMPP stanzas
менять security policy
```

Все действия проходят:

```text
LLM
 ↓
Tool call
 ↓
Schema validation
 ↓
Authorization
 ↓
Capability check
 ↓
Confirmation (если требуется)
 ↓
Execution
 ↓
Result sanitization
 ↓
LLM
```

---

# 66. Итоговая модель

В конечном результате должен получиться не простой XMPP chatbot.

Это должен быть:

```text
                    ┌──────────────────┐
                    │    XMPP users    │
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │   Slixmpp 1.10   │
                    └────────┬─────────┘
                             │
                 ┌───────────▼───────────┐
                 │     Agent Runtime     │
                 ├───────────────────────┤
                 │ sessions              │
                 │ memory                │
                 │ personas              │
                 │ authorization         │
                 │ security              │
                 │ scheduler             │
                 └───────────┬───────────┘
                             │
                 ┌───────────▼───────────┐
                 │      Agent Loop       │
                 └───────────┬───────────┘
                             │
             ┌───────────────┼────────────────┐
             │               │                │
             ▼               ▼                ▼
           Skills          Memory          Subagents
             │               │                │
       ┌─────┼─────┐         │          ┌─────┴─────┐
       │     │     │         │          │           │
      Web  GitHub MCP       RAG       researcher  coder
       │     │     │         │
       └─────┴─────┴─────────┴───────────────┐
                                             │
                                             ▼
                                            LLM
```

---

# 67. Definition of Done

Проект считается завершённым для первой production-версии только если:

* XMPP C2S работает;
* XEP-0114 component mode работает;
* reconnect работает;
* authorization работает;
* DM работает;
* MUC работает;
* XEP-0085 работает;
* sessions работают;
* JSONL persistence работает;
* memory работает;
* agentic loop работает;
* tools работают;
* web search работает;
* URL fetch работает;
* attachments работают;
* personas работают;
* Ollama работает;
* OpenAI-compatible provider работает;
* security capability checks работают;
* prompt injection protection работает;
* quotas работают;
* tests проходят;
* Docker deployment работает;
* configuration documented;
* secrets не попадают в logs/history;
* нет критических TODO/mock implementations.

---

# 68. Порядок работы coding-agent

Перед написанием большого количества кода:

1. Создай архитектурный план.
2. Создай структуру проекта.
3. Реализуй Phase 1.
4. Запусти tests.
5. Исправь ошибки.
6. Только после этого переходи к Phase 2.
7. После каждой фазы обновляй README.
8. Не ломай уже реализованные функции.
9. Не переписывай рабочие модули без необходимости.
10. После каждой крупной функции добавляй тест.

Если существующий код уже есть, сначала изучи его полностью и сохраняй существующее поведение.

Не удаляй функциональность ради упрощения.

При конфликте между roadmap и безопасностью приоритет:

```text
security
>
data isolation
>
correctness
>
reliability
>
roadmap feature
```

Сначала реализуй рабочий фундамент, затем расширяй его skills, memory, personas, MCP, sub-agents и federation.

Конечная цель — Python/XMPP agent runtime, функционально соответствующий архитектурной идее Fluux Agent, но построенный на Slixmpp 1.10.0 и пригодный для дальнейшего развития в полноценную federated XMPP agent platform.
