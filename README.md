# Todoist Q&A Task Assistant

A personal Telegram bot that lets multiple users manage and query their own
Todoist tasks through natural conversation. Powered by an open LLM (Qwen) with
tool calling, the official Todoist MCP server, and PostgreSQL — hosted on
Railway with observability via Grafana.

Each user connects their own Todoist account with a personal API token. Tokens
are encrypted at rest, all data is isolated per user, and the bot detects and
replies in the user's language (English or Russian).

## How it works

```
Telegram message (private chat only)
        ↓
Check user is registered  →  load encrypted token, decrypt in memory
        ↓
Detect language (EN / RU)  →  load conversation history + fresh system prompt
        ↓
Fetch Todoist tool definitions (cached per token)
        ↓
LLM call with tool calling  →  execute any tool calls via Todoist MCP
        ↓
Second LLM call to formulate the answer  →  reply in the user's language
```

- **Chat:** Telegram (`python-telegram-bot`, async)
- **LLM:** Qwen (`qwen3.6-35b-a3b`) via the OpenAI-compatible NeuralDeep API
- **Tools/data:** Todoist MCP server (`https://ai.todoist.net/mcp`), JSON-RPC over SSE
- **Storage:** PostgreSQL (asyncpg at runtime, Alembic/psycopg2 for migrations)
- **Crypto:** Fernet token encryption (`cryptography`)

## Project layout

| Path | Responsibility |
|---|---|
| `config.py` | Loads & validates all configuration from the environment |
| `messages.py` | All user-facing strings (EN + RU) |
| `language.py` | Language detection / resolution |
| `logger.py` | Stdout structured logging + secret redaction |
| `crypto.py` | Fernet encrypt/decrypt of Todoist tokens |
| `db.py` | All database access (asyncpg pool); the only place SQL lives |
| `mcp.py` | Todoist MCP client: SSE parsing, schema conversion, per-token tool cache |
| `llm.py` | Orchestration: history, system prompt, tool-calling loop, rate limits |
| `bot.py` | Telegram entry point: handlers, state machine, background tasks, `main()` |
| `migrations/` | Alembic migrations (`001_init`, `002_add_indexes`) |
| `scripts/rotate_key.py` | Manual Fernet key-rotation CLI (run during downtime) |
| `tests/` | One test file per module |

## Configuration

All configuration is read through `config.py` — never `os.environ` directly.
Copy `.env.example` to `.env` and fill it in.

**Required:**

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Telegram bot token from @BotFather |
| `NEURALDEEP_API_KEY` | API key for the NeuralDeep LLM endpoint |
| `NEURALDEEP_API_URL` | LLM base URL (e.g. `https://api.neuraldeep.ru/v1`) |
| `DATABASE_URL` | PostgreSQL connection string |
| `SECRET_KEY` | Fernet key for token encryption — generate once, back up immediately |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

**Optional (defaults shown):**

| Variable | Default | Description |
|---|---|---|
| `MCP_TOOLS_TTL` | `86400` | Tool-definition cache TTL (seconds) |
| `MAX_HISTORY_MESSAGES` | `20` | Conversation rows kept per user |
| `CONVERSATION_RETENTION_DAYS` | `7` | Daily-cleanup age cutoff for conversations |
| `LOG_RETENTION_DAYS` | `30` | Daily-cleanup age cutoff for logs |
| `LLM_MODEL` | `qwen3.6-35b-a3b` | LLM model name (lowercase — see note below) |
| `MCP_SERVER_URL` | `https://ai.todoist.net/mcp` | Todoist MCP endpoint |
| `TODOIST_BASE_URL` | `https://api.todoist.com/api/v1` | Todoist REST base for token validation |

> **Generate a `SECRET_KEY`:**
> ```bash
> python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
> ```
> If `SECRET_KEY` is lost, all stored tokens become permanently unrecoverable.
> Back it up to a password manager before deploying.

## Local development

A virtual environment lives in `.venv`. Activate it before any Python command.

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

Apply database migrations:

```bash
alembic upgrade head
```

Run the bot:

```bash
python bot.py
```

## Tests

Tests run against a **real** PostgreSQL test database (never production) pointed
to by `TEST_DATABASE_URL`. Each test runs in a transaction that is rolled back,
and migrations run once per session.

Start a local Postgres (Homebrew or Docker), then:

```bash
# Homebrew
brew services start postgresql@16
createdb taskassistant_test
export TEST_DATABASE_URL="postgresql://$(whoami)@localhost:5432/taskassistant_test"

# Docker (alternative)
# docker run -e POSTGRES_PASSWORD=test -p 5432:5432 postgres:16
# export TEST_DATABASE_URL="postgresql://postgres:test@localhost:5432/postgres"

pytest
```

Coverage gate: 80% overall (70% for `bot.py`). If `TEST_DATABASE_URL` is unset,
the `db` tests are skipped.

## Deployment (Railway)

The release command runs migrations before starting the bot:

```bash
alembic upgrade head && python bot.py
```

A migration failure aborts startup, so a broken deploy is reported rather than
silently running against the wrong schema. Set all required environment
variables in the Railway service, and use Railway's managed PostgreSQL add-on
for `DATABASE_URL`.

## Key rotation

To re-encrypt every stored token onto a new Fernet key (run during downtime):

```bash
python -m scripts.rotate_key --old-key <OLD_KEY> --new-key <NEW_KEY>
```

Re-encryption is idempotent and failure-isolated per user, so a re-run safely
retries any rows a previous run skipped. It does not delete the old key.

## Notes

- The LLM model name must be lowercase (`qwen3.6-35b-a3b`); the capitalised form
  is rejected by NeuralDeep keys with HTTP 401.
- The bot operates in **private chats only**; in a group it replies that it works
  only in private and takes no further action.
- Documentation: see `business-brief.md` (product) and `technical-spec.md`
  (contracts, schema, error rules). `interfaces.md` records every module's public
  surface.
