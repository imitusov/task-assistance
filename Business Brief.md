# Todoist Q&A Task Assistant — Brief v10

## Companion Document
Implementation contracts (module responsibilities, function signatures, database
schema, error handling rules) are defined in `technical-spec-v1.md`. Read this
brief first for context and decisions, then the spec for implementation detail.

## Versioning
This document is versioned manually. A new version is created when any
architectural decision, security model, or scope boundary changes.
Cosmetic edits do not increment the version.

---

## Goal
Build a personal Telegram bot that lets multiple users manage and query their own
Todoist tasks through natural conversation, powered by an open LLM with tool
calling. Hosted on Railway with full observability via Grafana.

---

## Problem Being Solved
Managing tasks through the Todoist app requires context switching. This bot brings
task management into Telegram — where the user already communicates — allowing
natural language queries and updates without opening Todoist.

---

## Users
- Multiple independent users, each with their own Todoist account
- Each user's data is fully isolated from other users
- Bot operates in private chats only. If added to a group chat, it responds with
  a message stating it only works in private conversations and takes no further
  action
- Bot is open to anyone who finds it on Telegram and provides a valid Todoist
  API token
- **Telegram username:** @aimitusov
- **Supported languages:** English and Russian. The bot detects the user's
  language from their first message and responds in that language throughout
  the conversation. Language can be changed at any time by writing in a
  different language. System prompt and all user-facing messages (errors,
  confirmations, onboarding) are provided in both languages

---

## Architecture Overview

```
Telegram message (private chat only)
        ↓
Bot checks user is registered
        ↓
Load user's encrypted token → decrypt in memory
        ↓
Detect user language (EN or RU)
        ↓
Load conversation history + inject fresh system prompt in detected language
        ↓
Retrieve tool definitions from cache (or MCP server on miss)
        ↓
Send to Qwen LLM with tools
        ↓
LLM decides: call tool(s) or answer directly
        ↓ tool call(s)              ↓ direct answer
Execute via MCP server          Return answer immediately
Append results to history
Ask LLM to formulate answer
        ↓
Save updated history to database
Log the interaction
        ↓
Send answer to user in Telegram
        ↓
Grafana reads logs from database → dashboards update
```

---

## Tech Stack

| Component | Choice | Reason |
|---|---|---|
| Chat interface | Telegram Bot (python-telegram-bot) | User already uses Telegram |
| LLM | Qwen3.6-35b-a3b | Excellent tool calling, fast MoE architecture |
| LLM API | `https://api.neuraldeep.ru/v1` | Available endpoint, OpenAI-compatible format |
| Task data | Todoist MCP server (`https://ai.todoist.net/mcp`) | Official tool definitions with full schemas |
| Database | PostgreSQL (Railway managed) | Handles concurrent users, JSONB for flexible storage |
| DB migrations | Alembic | Version-controlled schema changes, safe deploys |
| Encryption | Fernet (cryptography library) | Symmetric encryption for tokens at rest |
| Observability | Grafana Cloud free tier | Connects directly to PostgreSQL, no extra infrastructure |
| Hosting | Railway | Simple deploys, managed PostgreSQL add-on |
| Language | Python 3.11+ | Async support, rich ecosystem |

---

## Key Concepts

### What is the Todoist MCP Server
The Todoist MCP server is an official service operated by Todoist at
`ai.todoist.net`. It exposes Todoist functionality as a set of callable tools
following the Model Context Protocol (MCP) standard — an open protocol for
connecting AI models to external services. The bot connects to it directly as
an external API. No self-hosting or maintenance is required. Authentication uses
the user's personal Todoist API token passed as a Bearer header.

### What is OpenAI-Compatible Format
The LLM API follows the OpenAI Chat Completions standard. Requests are sent as
POST to `/v1/chat/completions` with a JSON body containing `model`, `messages`,
and optionally `tools`. Responses contain a `choices` array where each choice
has a `message` field that may include `content` (a text answer) or `tool_calls`
(a list of tool invocations the LLM wants executed). This format is widely
supported by open LLM providers and does not require the OpenAI SDK.

### What is a Conversation Turn
A conversation turn begins when the user sends a message and ends when the bot
sends its response. A turn includes all tool calls and LLM calls made to produce
that response. Failed turns — where the bot returns an error message rather than
a task-related answer — are still logged as complete turns with an error flag.
Total turn latency is measured from the moment the user's message is received to
the moment the bot's response is sent.

### What is Fernet Encryption
Fernet is a symmetric encryption scheme from the Python `cryptography` library.
It uses AES-128-CBC with HMAC for authentication. AES-128 provides sufficient
security for API tokens at rest — the primary threat model is database
compromise, not brute-force key attack, and AES-256 offers no practical
advantage here while requiring a lower-level cryptography API. A randomly
generated `SECRET_KEY` is used to encrypt tokens before storing them in
PostgreSQL and to decrypt them when needed at runtime. Without the key,
encrypted tokens are permanently unrecoverable.

---

## Pre-Development Verification

Before writing any application code, two things must be verified:

**1. Qwen tool calling**
Confirm that `api.neuraldeep.ru/v1` accepts OpenAI-compatible tool calling
format and returns `tool_calls` in the response. If the format differs, the
LLM layer must be adjusted before proceeding.

**2. MCP server SSE parsing**
The Todoist MCP server returns responses in Server-Sent Events (SSE) format,
not plain JSON. The MCP client must correctly parse this format. Authentication
is confirmed working with Bearer token and the correct Accept headers
(`application/json, text/event-stream`).

Both verifications have dedicated test scripts in the `tests/` directory that
must be run and pass before development starts.

---

## MCP Server

```
Endpoint:  https://ai.todoist.net/mcp
Protocol:  JSON-RPC 2.0, responses in SSE format
Auth:      Bearer token (user's Todoist API token)
Headers:   Content-Type: application/json
           Accept: application/json, text/event-stream
```

Tool definitions are fetched from the MCP server, converted to OpenAI function
calling format, and cached per Todoist token for 24 hours. When a user updates
their token via `/token`, the old cache entry is evicted immediately and a fresh
fetch occurs on the next message.

---

## Core Tools Used

Ten tools cover all Q&A and task management needs:

| Tool | Purpose |
|---|---|
| `find-tasks` | Filter by date, priority, project, label |
| `find-tasks-by-date` | Tasks for today or a date range |
| `find-projects` | Reference projects by name |
| `search` | Free-text search across all tasks |
| `get-productivity-stats` | Completion statistics |
| `add-tasks` | Create tasks from natural language |
| `update-tasks` | Rename, reprioritize, move tasks |
| `complete-tasks` | Mark tasks done |
| `reschedule-tasks` | Change dates, preserves recurring rules |
| `delete-object` | Delete a task (requires user confirmation first) |

---

## Multi-User Support

Each Telegram user has fully isolated context: their own encrypted Todoist API
token, their own conversation history, their own detected language, and their
own activity logs. No data crosses between users. Each MCP call uses the
requesting user's own token.

New users go through an onboarding flow triggered by `/start`.

---

## Language Support

The bot supports English and Russian. Language is detected automatically from
the user's message text using a lightweight detection method (no external API
required). The detected language is:

- Used to select the correct system prompt variant (EN or RU)
- Used to select the correct user-facing message (errors, confirmations,
  onboarding prompts, command descriptions)
- Stored per user in the database so the bot remembers their language across
  sessions
- Updated automatically if the user switches to writing in the other language

All hardcoded bot messages (onboarding, error responses, confirmations, command
help text) must be maintained in both English and Russian. No other languages
are supported in v1.

**Language detection:** Use the `langdetect` library for lightweight offline
detection. If detection confidence is low or the language is neither EN nor RU,
default to English.

---

## Conversation State Machine

The bot tracks three per-user states held in memory:

| State | Meaning |
|---|---|
| `NORMAL` | Default state — regular task query flow |
| `AWAITING_TOKEN` | Next non-command message treated as Todoist API token |
| `AWAITING_RESET` | Next message of `confirm` clears conversation history |

**Important:** States are held in memory and do not survive bot restarts. If
Railway redeploys while a user is mid-onboarding, their `AWAITING_TOKEN` state
is lost. The user must send `/start` again to re-enter the token flow. This is
an accepted limitation in v1.

**State transitions:**

```
/start (new user)     → AWAITING_TOKEN
token accepted        → NORMAL
token rejected        → AWAITING_TOKEN (stays, user tries again)
/token                → AWAITING_TOKEN
/reset                → AWAITING_RESET
"confirm" received    → NORMAL (history cleared)
any other message     → NORMAL (reset cancelled)
bot restart           → NORMAL (all states reset)
```

---

## Onboarding Flow

When a new user sends `/start`, the bot displays a welcome message in their
detected language with instructions to find their Todoist API token and a
security notice explaining that their token message will be immediately deleted.
The bot enters `AWAITING_TOKEN` state.

While in `AWAITING_TOKEN` state:
- Any non-command message is treated as a token submission attempt
- Regular task messages are not processed
- Sending `/start` again repeats the onboarding message and keeps the state
- The state persists until a valid token is successfully saved or the bot
  restarts

**Token submission:**
The bot makes a single GET request to the Todoist API to validate the token.
If validation returns HTTP 200, the token is accepted. If it fails for any
reason (wrong token, network error, any other status), the user is told the
token is invalid and asked to try again in their detected language. There is
no retry limit in v1.

Once accepted, the token is encrypted and saved, state returns to `NORMAL`,
and the user is confirmed connected.

**Existing users** who send `/start` see their current connection status in
their stored language and are not prompted for a token again.

---

## Security

### Token Encryption
Todoist API tokens are encrypted using Fernet before being stored in PostgreSQL.
They are decrypted in memory only when needed for an API call and never logged
or persisted in plain form.

`SECRET_KEY` is generated once using the `cryptography` library. After
generation, copy it immediately to a password manager entry (1Password,
Bitwarden, or equivalent) before setting it in Railway. Do not store it in a
file, email, or chat message. Then set it as a Railway environment variable.

**If `SECRET_KEY` is lost:** All stored tokens become permanently unrecoverable.
There is no way to decrypt them without the original key. Every registered user
would need to re-enter their Todoist token. Back up the key before doing
anything else.

### Token Message Deletion
When a user sends their Todoist token to the bot, the message is deleted from
Telegram immediately — before any other processing. If deletion fails (message
too old, permissions issue), the user is instructed in their language to delete
it manually. The token submission flow continues regardless of whether deletion
succeeded.

Accidental token sends outside the onboarding flow are also detected (a
40-character alphanumeric string sent while in `NORMAL` state) and deleted
automatically with a notification in the user's detected language.

### Log Sanitization
All log entries are sanitized before writing. Fields with sensitive names
(token, api_key, secret, password) are replaced with a redacted placeholder.
The plain token never appears in any log at any level.

### Grafana Database Access
Grafana connects to PostgreSQL using a dedicated read-only database user
(`grafana_reader`) with SELECT-only permissions on all tables. The main
application credentials are never shared with Grafana. SSL mode must be set
to `require` in the Grafana PostgreSQL data source settings. Do not use
`verify-full` as Railway's SSL certificates may not match the hostname format
expected by Grafana. Host, port, and database name are parsed from
`DATABASE_URL`.

---

## Conversation History

Each user's conversation is stored in PostgreSQL and loaded on every message.
The system prompt — which includes today's date and is in the user's language —
is always rebuilt fresh and prepended to history at the start of each turn.
It is never stored in the database; a stored version would have the wrong date
after midnight.

History is bounded in two ways:

**Per-write trim:** After every message is saved, older messages beyond the
configured limit (default 20) are deleted atomically in the same database
transaction as the insert. The user always has at most 20 messages stored.

**Daily cleanup:** A background job running at 3:00 UTC deletes all conversation
rows older than 7 days. The job calculates seconds until next 3:00 UTC using
`datetime.now(timezone.utc)` regardless of the server's local timezone,
ensuring consistent behaviour across Railway infrastructure regions.

Users can clear their history at any time using the two-step reset flow
described under Telegram Commands.

---

## LLM Behaviour

The LLM receives the conversation history plus tool definitions on every turn.
It either calls one or more tools, or answers directly without tools.

**When tools are called:** All tool calls in the response are executed before
the LLM is called a second time to formulate the final answer. This handles
cases where the LLM decides to call multiple tools in one turn (for example,
fetching tasks and productivity stats simultaneously).

If any tool call fails during a multi-tool turn, the entire turn is aborted
immediately and the user receives an error message in their language. Partial
results from successful tool calls in the same turn are discarded.

**When no tools are called:** The LLM's response is used directly as the
answer. No second LLM call is made.

---

## Rate Limits

The NeuralDeep API enforces two independent request windows:

**Session window** — a rolling 3-hour UTC window. On exhaustion the API returns
HTTP 429 with header `X-Window: session` and `Retry-After: 7200` (2-hour
cooldown before the window resets).

**Week window** — an ISO calendar week. On exhaustion the API returns HTTP 429
with header `X-Window: week` and `Retry-After` set to seconds until Monday
00:00 UTC.

Specific request count limits are not published by NeuralDeep. The correct
approach is to read response headers on every API call. Every response includes
`X-Tier`, `X-Window`, and `Retry-After` headers regardless of success or
failure.

**Bot behaviour on rate limit hit:**

1. Read `X-Window` to determine which limit was hit (session or week)
2. Read `Retry-After` to get the exact wait time in seconds
3. Convert `Retry-After` to a human-readable time and notify the user
   immediately in their language:
   - Session limit (EN): "I've reached my request limit. Please try again
     after [time]."
   - Session limit (RU): "Достигнут лимит запросов. Попробуйте снова после
     [время]."
   - Week limit (EN): "I've reached my weekly request limit. I'll be
     available again on Monday at 00:00 UTC."
   - Week limit (RU): "Достигнут недельный лимит запросов. Я снова буду
     доступен в понедельник в 00:00 UTC."
4. Log the event as `rate_limit` at WARNING level (not ERROR — this is
   expected behaviour, not a failure)
5. Do not retry automatically — cooldown periods are too long for transparent
   retry to be useful to the user

Rate limit events are surfaced in Grafana to monitor usage patterns and decide
when to upgrade the subscription tier.

---

## Background Tasks

Three tasks run continuously alongside the bot:

**Heartbeat** — logs an "alive" signal every 60 seconds. Grafana alerts if
this signal stops for 3 minutes, indicating the bot is down. This replaces
alerting on user activity, which produces false alarms during quiet periods
such as nights and weekends when no users are active.

**Daily cleanup** — runs at 3:00 UTC every day. Deletes conversation rows
older than 7 days and log rows older than 30 days. Both retention periods are
configurable via environment variables. The number of rows deleted is logged
for visibility in Grafana.

**Keep typing** — refreshes Telegram's typing indicator every 4 seconds while
waiting for the LLM response. Telegram's typing indicator expires after 5
seconds without a refresh, so without this task the indicator would disappear
mid-processing. The task is always cancelled when a response is ready, whether
the response succeeded or failed.

---

## Logging

All interactions are logged to two destinations simultaneously:

**Stdout** — captured by Railway's log viewer for real-time debugging. Every
log line is a single JSON object for easy filtering.

**PostgreSQL** — read by Grafana for dashboards and alerts. Log rows older
than 30 days are deleted by the daily cleanup job.

Key events logged: user messages, tool calls (with latency), LLM calls (with
stage and token count), bot responses (with total turn latency), rate limit
hits (with window type and retry-after), heartbeats, daily cleanup results,
and errors with full tracebacks. All data fields are sanitized before writing.

---

## Response Truncation

Telegram has a hard limit of 4096 characters per message. Responses exceeding
this limit are split and sent as multiple sequential messages using the
following priority:

1. Split at the last double newline (`\n\n`) before the 4096-character limit
2. If no double newline exists before the limit, split at the last single
   newline (`\n`) before the limit
3. If no newline exists at all before the limit, split at exactly 4096
   characters

This process repeats for each resulting chunk until all parts are under 4096
characters.

---

## Grafana Dashboards

Four dashboards built directly on the PostgreSQL logs table. No additional
infrastructure is required — Grafana Cloud's free tier connects to Railway
PostgreSQL directly.

**Grafana connection:** Use `grafana_reader` read-only credentials. Set SSL
mode to `require`. Parse host, port, and database name from `DATABASE_URL`.

**Uptime calculation:** Uptime is the percentage of one-minute windows in the
last 24 hours that contained at least one heartbeat event. This is resilient
to timing drift and restart jitter, unlike a raw count divided by 1440.

**Usage Overview** — active users, message volume, new registrations over time.

**Tool Analytics** — most used tools, call frequency over time, average latency
per tool, success rate.

**Performance** — average and P95 response latency, LLM token usage, latency
trends over time.

**Health and Uptime** — uptime percentage from heartbeat logs, heartbeat
timeline where gaps indicate downtime, error count and rate, recent error
details, cleanup statistics, rate limit hits over time split by window type
(session vs week).

**Alerts** delivered via Telegram:
- No heartbeat event in 3 minutes → bot is down
- More than 5 errors in 10 minutes → high error rate
- Average response latency above 10 seconds in 15 minutes → performance
  degradation

---

## Telegram Commands

| Command | Action |
|---|---|
| `/start` | Onboarding for new users; connection status for existing users |
| `/token` | Update Todoist API token — message deleted immediately on receipt |
| `/reset` | Sets state to `AWAITING_RESET`, prompts user to type `confirm` |
| `/refresh` | Reconnect to Todoist (use if the bot stops recognising your projects or tasks after changes in Todoist) |
| `/help` | List available commands with descriptions |

All command responses are delivered in the user's detected language.

**Reset flow:** `/reset` sets state to `AWAITING_RESET` and prompts the user
to type the word `confirm` as their next message. Typing `confirm` clears the
history and returns state to `NORMAL`. Any other message cancels the reset and
returns state to `NORMAL` with a cancellation notice. This avoids the
non-standard `/reset confirm` command pattern which Telegram clients do not
recognise as a command.

**Telegram error handling:** python-telegram-bot handles transient Telegram API
errors automatically with exponential backoff in polling mode. No additional
error handling is needed for temporary Telegram unavailability.

---

## Railway Hosting

**Service type:** Worker (polling mode — simpler than webhook for v1)
**Add-ons:** Railway managed PostgreSQL

**Config fail-fast:** If any required environment variable is missing, the bot
process exits with a non-zero status code during startup — before connecting to
Telegram or the database. Railway treats this as a failed deploy and does not
replace the running instance, preserving the last working version. The missing
variable name appears in Railway's deploy log.

**Deployment flow:**
1. Push to GitHub
2. Railway builds via Nixpacks
3. Alembic runs all pending migrations automatically
4. Bot process starts — config loaded, exits immediately if required vars missing
5. Database connection pool initialised
6. Background tasks started (heartbeat, daily cleanup)
7. Bot begins polling Telegram

**Graceful shutdown:** On SIGTERM (sent by Railway during deploys and restarts),
the bot stops accepting new messages and closes the database connection pool
cleanly before exiting.

**In-flight requests during shutdown:** Any turn in progress at shutdown time
is abandoned. If a tool call (such as completing or deleting a task) was already
executed before shutdown, the action is permanent — the bot will not be able to
notify the user. This is an accepted risk in v1. Users who do not receive a
response should check Todoist directly to confirm whether an action was applied.

---

## Project Structure

```
todoist-bot/
├── .gitignore          — excludes .env, __pycache__, *.pyc, .venv
├── bot.py              — entry point, handlers, state machine, background tasks
├── llm.py              — LLM client, tool calling loop, system prompt (EN + RU)
├── mcp.py              — MCP client, SSE parser, schema converter, tool cache
├── db.py               — PostgreSQL connection pool, all database queries
├── crypto.py           — token encryption and decryption
├── logger.py           — structured JSON logging, log sanitization
├── config.py           — all environment variables, fail-fast on missing
├── messages.py         — all user-facing strings in EN and RU
├── scripts/
│   └── rotate_key.py   — manual key rotation utility
├── migrations/
│   ├── alembic.ini
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       ├── 001_init.py
│       └── 002_indexes.py
├── tests/
│   ├── test_qwen_tools.py  — pre-dev verification of tool calling format
│   └── test_mcp_auth.py    — pre-dev verification of MCP SSE parsing
├── requirements.txt
└── railway.toml
```

Note: `messages.py` is a new module containing all hardcoded user-facing strings
in both EN and RU. No user-facing string appears hardcoded anywhere else. This
ensures consistent bilingual support and makes future language additions
straightforward.

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | — | From @BotFather |
| `NEURALDEEP_API_KEY` | ✅ | — | Qwen API key |
| `NEURALDEEP_API_URL` | ✅ | — | `https://api.neuraldeep.ru/v1` |
| `DATABASE_URL` | ✅ | — | Auto-set by Railway PostgreSQL |
| `SECRET_KEY` | ✅ | — | Fernet key — generate once, back up to password manager immediately |
| `LOG_LEVEL` | ✅ | `INFO` | `INFO` or `DEBUG` |
| `MCP_TOOLS_TTL` | — | `86400` | Tool cache TTL in seconds |
| `MAX_HISTORY_MESSAGES` | — | `20` | Max stored messages per user |
| `CONVERSATION_RETENTION_DAYS` | — | `7` | Hard delete conversations after N days |
| `LOG_RETENTION_DAYS` | — | `30` | Hard delete logs after N days |

---

## Acceptance Criteria

The bot is ready for v1 launch when:

- A new user can complete onboarding end-to-end in under 2 minutes
- Natural language task queries return correct results in under 5 seconds
- Token messages are deleted within 1 second of receipt
- Bot responds correctly in English when messaged in English
- Bot responds correctly in Russian when messaged in Russian
- Bot survives a Railway redeploy with no data loss
- Rate limit responses are caught, logged at WARNING level, and communicated
  to the user clearly in their language
- Grafana dashboards show live data within 5 minutes of first user message
- All pre-development verification tests pass

---

## Out of Scope (v1)

| Feature | Reason excluded |
|---|---|
| Reminders and push notifications | Requires persistent scheduling infrastructure not available in v1 |
| Todoist Goals and workspace features | Low usage relative to implementation cost |
| Voice message support | Requires speech-to-text integration — separate workstream |
| Per-user rate limiting | No abuse expected at this scale; add in v2 if needed |
| Admin whitelist or invite-only access | Open access acceptable for v1 user base |
| Automated key rotation | Manual rotation script sufficient for v1 |
| Languages beyond EN and RU | Single user base in v1; add languages when user base grows |
| Webhook mode | Polling is simpler to deploy on Railway; migrate in v2 if latency becomes an issue |
| State persistence across restarts | In-memory state is sufficient for v1; add database-backed state in v2 if onboarding drop-off becomes measurable |
