# Todoist Q&A Task Assistant — Technical Specification v8

## Purpose
This document defines implementation contracts for each module. It is written for
the developer or agent implementing the code. Read `brief-v10.md` first for
context and decisions. This spec defines what each module must do — not how to
do it.

## Companion Document
Product scope, architectural decisions, and rationale are in `brief-v10.md`.
When this spec and the brief conflict, the brief takes precedence.

---

## One-Time Manual Setup (prerequisites — complete before first deploy)

**1. Create `grafana_reader` PostgreSQL user**
```sql
CREATE USER grafana_reader WITH PASSWORD 'choose_a_strong_password';
GRANT SELECT ON ALL TABLES IN SCHEMA public TO grafana_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO grafana_reader;
```

**2. Generate and back up `SECRET_KEY`**
```
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
Copy to password manager immediately. Set as Railway environment variable.

---

## Pre-Development Verification

Both scripts must be run manually before any application code is written.
Both must exit with code 0 to be considered passing. A non-zero exit code
blocks development — do not proceed until both pass.

**Partial failure is failure.** If `test_qwen_tools.py` confirms tool calling
works but thinking tokens are present and unhandled, that is not a pass —
document the finding, update the spec's thinking token section, and re-run.
If `test_mcp_auth.py` confirms auth works but SSE parsing returns unexpected
structure, that is not a pass — update the `mcp.py` SSE parser contract
before proceeding.

Each script must print a clear `PASS` or `FAIL` summary line as its last
output so the result is unambiguous in CI logs or terminal output.

### test_qwen_tools.py
Verify `api.neuraldeep.ru/v1` accepts OpenAI tool calling. Send a request
with a dummy tool and assert response contains `tool_calls`. Verify whether
`<think>...</think>` blocks appear in responses — document position relative
to `tool_calls`. Print full raw response. Exit 0 on pass, exit 1 on any
assertion failure, unexpected response format, or network error.

### test_mcp_auth.py
Verify Todoist MCP server accepts Bearer token auth and returns SSE tool
definitions. Assert at least one tool with `name` and `inputSchema`. Exit 0
on pass, exit 1 on auth failure, SSE parse failure, or network error.

---

## Unit Tests

Unit tests live in `tests/`. Run with `pytest`. All tests must pass before
any merge or deploy. Use `pytest-asyncio` for async cases and
`unittest.mock` for patching. No real network calls in unit tests — all
external services mocked.

**Coverage thresholds** enforced via `pyproject.toml` or `pytest.ini`:
```
[tool.pytest.ini_options]
addopts = "--cov=. --cov-fail-under=80"
asyncio_mode = "auto"
```
`asyncio_mode = "auto"` means all async test functions are automatically
treated as asyncio tests without requiring the `@pytest.mark.asyncio`
decorator on each one. This setting is required — without it,
`pytest-asyncio` operates in `strict` mode by default in newer versions,
which causes collection warnings and requires manual decoration of every
async test. Set once in config, applies to all test files.
Minimum 80% for all modules. `bot.py` minimum 70% (Telegram dependency makes
full coverage impractical). Failing below threshold blocks deployment the
same way a failing test does.

### tests/test_messages.py

**Cases to cover:**
- `get("token_accepted", "en")` returns non-empty English string
- `get("token_accepted", "ru")` returns non-empty Russian string
- `get("rate_limit_session", "en", retry_time="2 hours")` contains `"2 hours"`
- `get("rate_limit_session", "ru", retry_time="2 часа")` contains `"2 часа"`
- `get("unknown_key", "en")` raises `KeyError`
- `get("token_accepted", "fr")` returns English string (unknown lang fallback)
- `get("reset_confirmed", "en", count=5)` contains `"5"`
- All required keys exist for both `"en"` and `"ru"` — iterate full key
  list and assert no `KeyError`
- `len(get("send_error", "en")) < 100` — enforces truncation-safe constraint
- `len(get("send_error", "ru")) < 100` — same for Russian

### tests/test_language.py

**Cases to cover:**
- `detect("Hello, how are you today")` returns `"en"`
- `detect("Привет, как дела сегодня")` returns `"ru"`
- `detect("ok")` returns `"en"` (below `MIN_DETECTION_LENGTH`)
- `detect("")` returns `"en"` (empty string)
- `detect("9bba1be2b49ca2c9941ecea5cd3d8a0be3069845")` returns `"en"`
  (hex token — garbage detection defaults to EN)
- `detect` called twice with same input returns same result (deterministic)
- `resolve(None, "Hello world from me")` returns `("en", True)`
- `resolve("ru", "Привет мир как дела сегодня")` returns `("ru", False)`
- `resolve("en", "Привет мир как дела сегодня")` returns `("ru", True)`
- `resolve("ru", "ok")` returns `("ru", False)` (too short, no switch)
- `resolve("", "Hello world today")` returns `("en", True)` — empty string
  treated same as None (no language set). `resolve` must handle empty string
  identically to None. This is enforced here rather than via DB constraint
  so the module is defensive against bad data
- `resolve("en", None)` raises `TypeError` — document that caller must
  never pass None as text

### tests/test_crypto.py

**Cases to cover:**
- `decrypt_token(encrypt_token(plain))` returns `plain` (round-trip)
- `encrypt_token(plain)` output differs from `plain`
- `encrypt_token` output differs on each call (random IV)
- `decrypt_token("invalid_ciphertext")` raises descriptive error (not raw
  cryptography exception)
- Plain token does not appear in any exception message from `decrypt_token`
- Both functions work with 40-character hex string (realistic Todoist token)

### tests/test_logger.py

**Cases to cover:**
- `sanitize({"token": "abc123"})` returns `{"token": "***REDACTED***"}`
- `sanitize({"TOKEN": "abc123"})` returns redacted (case-insensitive)
- `sanitize({"todoist_token": "abc"})` returns redacted (key contains "token")
- `sanitize({"message": "hello"})` returns `{"message": "hello"}` (unchanged)
- `sanitize` does not mutate input dict
- `sanitize({"api_key": "x", "normal": "y"})` redacts only `api_key`
- `sanitize({"params": {"token": "secret"}})` does NOT redact the nested
  `token` key — top-level scan only, by explicit design rule. The caller
  must not pass sensitive values inside nested structures. This test
  documents the contract, not a shortcoming
- `log_stdout` writes single JSON line to stdout (capture with capsys)
- `log_stdout` output contains `timestamp`, `level`, `event`, `user_id`
- `log_stdout` calls `sanitize` — token in data is redacted in output

### tests/test_db.py

Uses a real PostgreSQL test database. Requires `TEST_DATABASE_URL` env var
pointing to a separate test database — never the production Railway database.

**Local dev setup:** Use Docker:
```
docker run -e POSTGRES_PASSWORD=test -p 5432:5432 postgres:16
TEST_DATABASE_URL=postgresql://postgres:test@localhost:5432/postgres
```

**CI setup:** Use GitHub Actions `services: postgres` container with matching
env var.

**Test isolation:** Every test runs inside a database transaction that is
rolled back after the test completes — whether it passed, failed, or raised.
Use a pytest fixture that opens an asyncpg connection, begins a transaction,
yields the connection for test use, then calls `ROLLBACK` in teardown. This
guarantees clean state for every test without relying on cleanup code that
may not run on failure.

Migrations run against `TEST_DATABASE_URL` once before the test suite starts,
via a session-scoped pytest fixture.

**Cases to cover:**

`get_user` / `save_user`:
- `get_user(999999)` returns `None`
- `save_user(id, token, "en")` then `get_user(id)` returns correct row
- `save_user` twice with same `id` updates token and language
- `last_active_at` updated on `save_user` conflict

`update_language`:
- Updates `language` field correctly
- Updates `last_active_at`
- Does not change `todoist_token`

`touch_user`:
- Updates only `last_active_at`
- Does not change `language` or `todoist_token`

`save_user_message` / `save_turn` / trim:
- After `save_user_message`: one user row exists, no trim occurred
- After `save_turn`: assistant row exists, trim ran exactly once
- Insert 25 user+assistant pairs: assert exactly `MAX_HISTORY_MESSAGES`
  rows remain
- Trim runs in `save_turn` only — `save_user_message` never trims
- `save_turn` preserves most recent rows

`save_tool_result`:
- Inserts row with `role = "tool"` — does not trim

`delete_turn_tool_results`:
- Deletes only `role = "tool"` rows with `created_at >= since`
- Does not delete user or assistant rows
- Does not delete tool rows created before `since`
- **Boundary condition (`>=`):** Insert tool row, record its `created_at`
  as `since`, call `delete_turn_tool_results(user_id, since)`, assert row
  IS deleted — boundary is inclusive by design
- Passing naive datetime raises (asyncpg rejects for TIMESTAMPTZ)
- No tool rows exist: silently deletes zero rows, no error

`get_history`:
- Returns rows in `created_at` ascending order
- Excludes `role = "system"` rows
- Returns raw JSONB content objects (list of dicts with `role` key)
- Returns `[]` for new user

`clear_user_history`:
- Returns 0 for user with no history (documents `reset_confirmed_empty` path)
- Returns correct count after inserting rows
- Deletes all roles (user, assistant, tool)

`cleanup_old_conversations` / `cleanup_old_logs`:
- Deletes rows older than `days` days
- Preserves rows newer than `days` days
- Returns correct count

`log`:
- Inserts sanitized row into logs table
- Calls `log_stdout` (mock stdout, assert called)
- Does not raise on database failure (mock db to raise, assert no exception)

### tests/test_mcp.py

**SSE parser:**
- Valid SSE with `data:` line returns parsed JSON
- No `data:` line raises descriptive error containing raw text
- Multiple lines returns only `data:` line content
- Leading/trailing whitespace on `data:` line is stripped

**Tool schema converter:**
- `inputSchema` renamed to `parameters`
- Wrapped in `{"type": "function", "function": {...}}`
- `name` and `description` passed through unchanged
- Nested schema properties passed through unchanged
- Empty `inputSchema` produces empty `parameters`

**Tool cache:**
- `get_tools(token)` fetches on first call
- `get_tools(token)` returns cached on second call (HTTP not called)
- Cache expires after `MCP_TOOLS_TTL` (mock `time.time`)
- **After TTL expiry:** Assert returned tools are in OpenAI format
  (`{"type": "function", "function": {...}}`), not raw MCP format
  (`{"name": ..., "inputSchema": ...}`). Prevents regression where
  cache miss returns unconverted format
- `evict_cache(token)` causes next `get_tools` to fetch again
- Different tokens have independent cache entries

**`call_tool`:**
- Returns `result` field from valid JSON-RPC SSE response
- Raises on HTTP error status
- Raises on SSE parse failure
- Raises on timeout (mock httpx timeout)
- Uses `httpx.Timeout(total=10.0)` — assert timeout parameter in request

### tests/test_llm.py

**Direct answer (no tools):**
- `save_user_message` called before LLM call (step 4)
- `save_turn` called exactly once
- `save_tool_result` never called
- Answer returned matches LLM response content

**Tool calling path:**
- One `tool_call` → `mcp.call_tool` called once, second LLM call made,
  `save_turn` called
- Two `tool_calls` → `mcp.call_tool` called twice, both results appended
  before second LLM call
- `save_tool_result` called once per tool call
- `save_turn` called exactly once after second LLM response

**`turn_start` propagation:**
- On tool failure: assert `delete_turn_tool_results` is called with the
  EXACT `turn_start` value passed into `process_message` — not a new
  timestamp generated inside the function. Use `unittest.mock` to capture
  the argument and compare identity
- On rate limit after tool calls: same assertion
- On LLM timeout after tool calls: same assertion
- On `save_tool_result` failure: same assertion

**Early return paths:**
- HTTP 429 first call → rate limit message, `save_turn` NOT called,
  `save_user_message` WAS called, `delete_turn_tool_results` NOT called
- HTTP 429 second call → `delete_turn_tool_results` called, rate limit message
- LLM timeout first call → `llm_timeout` returned
- LLM timeout second call → `delete_turn_tool_results` called
- Tool failure → `delete_turn_tool_results` called, remaining tool calls
  skipped, `tool_failure` returned
- `save_tool_result` failure → `delete_turn_tool_results` called,
  `tool_failure` returned

**Rate limit:**
- `X-Window: session` → `rate_limit_session` message
- `X-Window: week` → `rate_limit_week` message
- `rate_limit` logged at WARNING not ERROR
- `Retry-After` value appears in returned message

**Thinking tokens:** (run only if confirmed in `test_qwen_tools.py`)
- `<think>content</think>answer` → `answer` returned
- No thinking block → response unchanged

**`token_count`:**
- `response.usage.total_tokens` present → logged as integer
- `response.usage` absent → logged as `null`

**System prompt:**
- EN system prompt when `language="en"`
- RU system prompt when `language="ru"`
- `{current_date}` replaced with today's date

### tests/test_bot.py

**Group chat guard:**
- Group message → `group_chat_rejected` sent, no processing
- Private message → processing continues

**Per-user lock:**
- Second concurrent message while lock held → discarded silently
- Second concurrent command while lock held → `please_wait` sent
- Lock released after successful handler completion
- Lock released when handler raises unexpected exception (try/finally)
- **Keep-typing cancelled on unexpected exception:** Mock
  `llm.process_message` to raise. Assert keep-typing task is cancelled
  (finally block runs). Confirms finally block handles both lock and
  keep-typing correctly

**State machine — individual transitions:**
- `NORMAL` → regular message handler runs
- `AWAITING_TOKEN` → token handler runs for non-command message
- `AWAITING_RESET` + `"confirm"` → history cleared, state `NORMAL`
- `AWAITING_RESET` + `"CONFIRM"` → cleared (case-insensitive)
- `AWAITING_RESET` + other text → cancelled, state `NORMAL`
- `/start` new user → `AWAITING_TOKEN`
- `/start` existing user → `already_registered`, state unchanged
- `/token` → `AWAITING_TOKEN`

**State machine — sequences (multi-step flows):**
- `/start` → send token (valid) → assert `token_accepted` sent and state
  is `NORMAL`
- `/start` → send token (invalid) → assert `token_invalid` and state still
  `AWAITING_TOKEN` → send token (valid) → assert `token_accepted` and state
  `NORMAL`
- `/reset` → send `"confirm"` → assert history cleared and state `NORMAL`
- `/reset` → send other text → assert `reset_cancelled` and state `NORMAL`
- `/token` → send token (network error) → assert `token_network_error`
  and state `AWAITING_TOKEN` → send token (valid) → assert `token_accepted`

**Message handler — user load order:**
- User loaded (step 6) before `AWAITING_RESET` check (step 7) — stored
  language available for `reset_confirmed`/`reset_cancelled`
- `db.get_user` raises DB exception → `db_error` sent in detected language,
  handler returns cleanly

**Language handling:**
- Language changed → `update_language` called, `touch_user` NOT called
- Language unchanged → `touch_user` called, `update_language` NOT called

**Message handler — other paths:**
- Unregistered user → `unregistered` sent
- Accidental token (40-char hex) → message deleted, `token_accidental` sent
- `decrypt_token` raises → `decrypt_error` sent
- `turn_start` is timezone-aware datetime (assert `tzinfo is not None`)

**Token input handler:**
- Valid token → message deleted, `save_user` called, `token_accepted` sent
- Invalid (non-200) → `token_invalid`, state stays `AWAITING_TOKEN`
- Network error → `token_network_error`, state stays `AWAITING_TOKEN`
- Token deletion fails → `token_deletion_failed` sent, flow continues
- Existing user → `mcp.evict_cache` called with OLD token
- `detected_language` from `context.user_data` used (not from token string)
- `detected_language` cleared on success
- `detected_language` preserved on invalid token (retry uses same language)

**`_send_with_truncation`:**
- Under 4096 chars → single message
- `\n\n` before 4096 → split at `\n\n`
- `\n` (no `\n\n`) before 4096 → split at `\n`
- No newline before 4096 → hard cut at 4096
- `send_message` failure → `send_error` via direct `bot.send_message` (not
  `_send_with_truncation` recursively)
- Both `send_message` calls fail → logged to stdout only, no exception

**Command handlers:**
- `/reset` loads user, uses stored language
- `/reset` unregistered → `unregistered` sent, state NOT changed to
  `AWAITING_RESET`
- `/refresh` unregistered → `unregistered` sent
- `/help` registered → stored language used
- `/help` unregistered → detected language used

**Heartbeat task:**
- First sleep is 60s before first event (assert first `asyncio.sleep`
  arg is 60)
- Logs `heartbeat` at INFO
- Continues after log failure (mock `db.log` to raise)

**Daily cleanup task:**
- Calculates sleep duration using UTC time (mock `datetime.now(timezone.utc)`)
- Logs `daily_cleanup` at INFO on success
- Logs ERROR and continues on DB failure
- Task never terminates on error

---

## Module Contracts

---

### config.py

Loads all env vars at import time. Required vars raise `KeyError` immediately
if missing. All modules import from `config` — never `os.environ` directly.

**Required:** `TELEGRAM_BOT_TOKEN`, `NEURALDEEP_API_KEY`, `NEURALDEEP_API_URL`,
`DATABASE_URL`, `SECRET_KEY`, `LOG_LEVEL`

**Optional with defaults:**
- `MCP_TOOLS_TTL` → int, default 86400
- `MAX_HISTORY_MESSAGES` → int, default 20
- `CONVERSATION_RETENTION_DAYS` → int, default 7
- `LOG_RETENTION_DAYS` → int, default 30

---

### messages.py

Single source of truth for all user-facing strings in EN and RU. No string
hardcoded elsewhere. Single source for command descriptions.

`get(key: str, lang: str, **kwargs) → str`
- Unknown `key` raises `KeyError`
- Unknown `lang` falls back to `"en"` silently
- `**kwargs` substituted via `.format(**kwargs)`

**Note:** `please_wait` is the only message sendable before lock acquisition.

**Required keys:**

| Key | Placeholders | Description |
|---|---|---|
| `welcome` | — | Onboarding + token instructions + security notice |
| `token_accepted` | — | Token saved, message deleted |
| `token_invalid` | — | Token rejected, try again |
| `token_network_error` | — | Could not reach Todoist |
| `token_deletion_failed` | — | Could not delete token message |
| `token_accidental` | — | Accidental token detected and deleted |
| `already_registered` | — | Already connected |
| `unregistered` | — | Not set up, use /start |
| `rate_limit_session` | `retry_time` | Session limit hit |
| `rate_limit_week` | `retry_time` | Week limit hit |
| `llm_timeout` | — | LLM too slow |
| `tool_failure` | — | Todoist unreachable |
| `reset_prompt` | — | Type confirm |
| `reset_confirmed` | `count` | History cleared |
| `reset_confirmed_empty` | — | History already empty |
| `reset_cancelled` | — | Reset cancelled |
| `refresh_confirmed` | — | Reconnected to Todoist |
| `group_chat_rejected` | — | Private chats only |
| `help_text` | — | Full command list |
| `please_wait` | — | Processing in progress |
| `decrypt_error` | — | Re-register with /token |
| `send_error` | — | Under 100 chars — sent via direct bot.send_message |
| `db_error` | — | Temporary error, try again |

---

### crypto.py

**encrypt_token(plain: str) → str**
Fernet-encrypts using `SECRET_KEY`. Returns base64 string. Raises clear
error on failure. Never logs input.

**decrypt_token(encrypted: str) → str**
Decrypts Fernet string. Returns plain token. Raises descriptive error on
failure — not raw `cryptography` exception. Plain token never appears in
any exception message.

---

### logger.py

**sanitize(data: dict) → dict**
Scans top-level keys only. Does not recurse into nested dicts or lists.
This is an explicit design rule, not a limitation: log data must be
structured so that sensitive values appear only at the top level. Callers
are responsible for flattening or excluding nested sensitive data before
passing to `log`. Case-insensitive key scan. Keys containing `token`,
`api_key`, `secret`, or `password` → `"***REDACTED***"`. Returns new dict,
does not mutate input. Values that are themselves dicts or lists are passed
through unchanged regardless of their contents.

**Deviation (maintainer-confirmed 2026-06-25):** the literal substring rule
above self-conflicts with this spec's own `llm_call` field name
`token_count` (see Log Events table and the Grafana query
`data->>'token_count' ... ::int`) — `token_count` contains `"token"`, so a
literal implementation redacts it to the string `"***REDACTED***"`,
corrupting the metric and breaking the documented Grafana cast. Found in
production: real `llm_call` logs showed `"token_count": "***REDACTED***"`.
Resolution: `sanitize` carries a narrow, explicit exception for the exact key
`token_count` (not a general allowlist) so it passes through unredacted
while every other token/secret-shaped key is still caught.

**log_stdout(level, event, user_id, data)**
Single JSON line to stdout. Fields: `timestamp` (ISO 8601 UTC), `level`,
`event`, `user_id`, `data`. Calls `sanitize` on `data`. `user_id` may be
None. Respects `LOG_LEVEL` from config.

---

### db.py

All database access. Uses asyncpg pool. No SQL in any other module.

**Pool lifecycle**

`init_pool() → None` — Min 2, max 10 connections. Raises on failure —
caller exits non-zero.

`close_pool() → None` — Called in shutdown finally block.

**User functions**

`get_user(user_id: int) → dict | None`
Full user row or None. Fields: `telegram_user_id`, `todoist_token`
(encrypted), `language`, `created_at`, `last_active_at`. No separate
`get_user_token`.

`save_user(user_id: int, encrypted_token: str, language: str) → None`
Insert or update. On conflict: update token, language, `last_active_at`.

`update_language(user_id: int, language: str) → None`
Updates `language` AND `last_active_at`. Do not call `touch_user` in the
same turn.

`touch_user(user_id: int) → None`
Updates only `last_active_at`. Call only when `changed == False`.

`get_all_users() → list[dict]` — Key rotation script only.

`update_token(user_id: int, encrypted_token: str) → None` — Key rotation
script only.

**Conversation functions**

`get_history(user_id: int) → list[dict]`
Returns `[row["content"] for row in rows]` ordered `created_at` ascending,
excluding `role = "system"`. Raw JSONB objects passed directly to LLM.

`save_user_message(user_id: int, content: dict) → None`
Inserts user message row. Does NOT trim. Called at the start of every turn
including early returns, so user context is preserved for next turn.

`save_turn(user_id: int, assistant_content: dict) → None`
Inserts assistant row. Runs `MAX_HISTORY_MESSAGES` trim exactly once
atomically. THE ONLY location where trimming occurs. Called exactly once
per successful turn. `MAX_HISTORY_MESSAGES` counts all roles (user,
assistant, tool) — tool-heavy turns consume more slots.

`save_tool_result(user_id: int, tool_content: dict) → None`
Inserts tool result row. Does not trim. On failure: caller must call
`delete_turn_tool_results` and return `tool_failure`. Do not continue to
second LLM call with unpersisted tool results.

`delete_turn_tool_results(user_id: int, since: datetime) → None`
Deletes `role = "tool"` rows where `created_at >= since` (boundary
inclusive — row created at exactly `since` is deleted, by design). `since`
must be timezone-aware (`datetime.now(timezone.utc)`) — asyncpg rejects
naive datetimes. Safe to call with no matching rows. Per-user lock makes
`turn_start` overlap impossible — `since` guards against rows from
previous bot sessions only.

`clear_user_history(user_id: int) → int`
Deletes all conversation rows for this user (all roles). System messages
never stored so never counted. Returns count — 0 means no history existed.

`cleanup_old_conversations(days: int) → int`
`WHERE created_at < NOW() - make_interval(days => $1)`. Integer param,
no string interpolation. Returns rows deleted.

`cleanup_old_logs(days: int) → int` — Same pattern for logs.

`log(user_id: int | None, level: str, event: str, data: dict) → None`
Sanitizes, inserts log row, calls `log_stdout`. On insert failure: calls
`log_stdout` only. Never propagates.

**Content structure** (`role` column and `content.role` always identical):

- `user`: `{"role": "user", "content": "string"}`
- `assistant` (text): `{"role": "assistant", "content": "string"}`
- `assistant` (tool call): `{"role": "assistant", "content": null, "tool_calls": [{"id": "...", "type": "function", "function": {"name": "...", "arguments": "..."}}]}`
- `tool`: `{"role": "tool", "tool_call_id": "string", "content": "string"}`

Tool result `content`: `json.dumps(result)` from `mcp.call_tool`.

---

### language.py

`DetectorFactory.seed = 0` at import for determinism.

**Constants:** `SUPPORTED_LANGUAGES = {"en", "ru"}`, `DEFAULT_LANGUAGE = "en"`,
`MIN_DETECTION_LENGTH = 10`, `MIN_DETECTION_CONFIDENCE = 0.9`

**detect(text: str) → str**
Returns `"en"` or `"ru"` only. Returns `DEFAULT_LANGUAGE` on short text,
unsupported result, low confidence, or any exception. Never raises.

**resolve(stored_language: str | None, text: str) → tuple[str, bool]**
- `text` must not be None (caller's responsibility)
- Empty string `stored_language` treated identically to `None` — defensive
  against bad database data
- `True` in second element → caller must call `db.update_language`
- None or empty stored → `(detected, True)`
- Same as stored → `(stored, False)`
- Different and thresholds met → `(detected, True)`
- Different but thresholds not met → `(stored, False)`
- Never raises

---

### mcp.py

**SSE parsing:** Find `data:` line, strip prefix, decode JSON. Raise
descriptive error with raw text if no `data:` line found.

**Schema conversion:** Rename `inputSchema` → `parameters`, wrap in
`{"type": "function", "function": {...}}`. Pass all other fields through.

**Cache:** Per token. Return cached if younger than `MCP_TOOLS_TTL`.
Otherwise fetch, convert to OpenAI format, cache, return. Converted format
returned on both cache hit and cache miss — never raw MCP format.

`get_tools(token: str) → list` — OpenAI-format tools, cached.

`call_tool(token: str, name: str, arguments: dict) → dict`
Returns `result` from JSON-RPC response. Uses `httpx.Timeout(total=10.0)`
for full SSE receipt. Raises on failure or timeout.

`evict_cache(token: str) → None` — Removes token from cache silently.

---

### llm.py

Returns plain strings only. No Telegram knowledge.

**System prompts:** EN and RU templates with `{current_date}`. Never stored.

**Thinking tokens:** Strip `<think>...</think>` from responses if confirmed
by `test_qwen_tools.py`. Handle nested and incomplete blocks.

**Rate limit:** On HTTP 429: read `X-Window`, `Retry-After`, log `rate_limit`
at WARNING (never ERROR), return message from `messages.py`. No retry.

**`token_count`:** From `response.usage.total_tokens`. Log `null` if absent.

**process_message(user_id, user_text, token, language, turn_start) → str**

`turn_start`: timezone-aware datetime from `bot.py` after lock acquisition.

1. `db.get_history(user_id)`
2. Prepend system prompt in `language` with today's date
3. Build user message dict
4. `db.save_user_message(user_id, user_msg)` — always, before any LLM call
5. Append to in-memory history
6. `mcp.get_tools(token)`
7. LLM call with `tool_choice: "auto"`, 30s timeout. Log `llm_call`
   (stage: `"tool_selection"`, token_count)
8. HTTP 429 → rate limit, return message
9. Timeout → log WARNING, return `llm_timeout`
10. Strip thinking tokens if applicable
11. No tool calls → `db.save_turn`, log `bot_response`, return answer
12. Tool calls:
    a. Append assistant tool-call message to in-memory history
    b. For each tool call:
       - `mcp.call_tool`. On failure: log ERROR,
         `db.delete_turn_tool_results(user_id, turn_start)`,
         return `tool_failure`
       - Serialize result: `json.dumps(result)`
       - Build tool message dict
       - `db.save_tool_result`. On failure: log ERROR,
         `db.delete_turn_tool_results(user_id, turn_start)`,
         return `tool_failure`
       - Append to in-memory history
       - Log `tool_call` with name, params, latency_ms
    c. Second LLM call with `tool_choice: "auto"`, 30s timeout. Log
       `llm_call` (stage: `"answer_generation"`, token_count)
    d. HTTP 429 → `db.delete_turn_tool_results(user_id, turn_start)`,
       return rate limit message
    e. Timeout → `db.delete_turn_tool_results(user_id, turn_start)`,
       return `llm_timeout`
    f. Strip thinking tokens
    g. `db.save_turn(user_id, assistant_dict)`
    h. Log `bot_response` with total latency
    i. Return answer text

---

### bot.py

**States** (in-memory, lost on restart):
`NORMAL`, `AWAITING_TOKEN`, `AWAITING_RESET`

**Per-user lock pattern** (ALL handlers):
```
lock = context.user_data.setdefault("lock", asyncio.Lock())
if lock.locked():
    # message handler: discard silently
    # command handlers: send please_wait
    return
await lock.acquire()
try:
    ...processing...
finally:
    lock.release()
```
TOCTOU window negligible in single-threaded async Python.

**Group chat guard:** `update.effective_chat.type == "private"` at top of
every handler, before lock. Send `group_chat_rejected` if not private.

**Startup:**
1. `db.init_pool()` — failure: log stdout, exit non-zero
2. Start heartbeat task
3. Start daily cleanup task
4. Register SIGTERM/SIGINT handlers
5. Start polling

**Shutdown:** Stop polling, `db.close_pool()` in finally block.

**Message handler:**
1. Private chat guard
2. Acquire lock — contention: discard silently
3. `turn_start = datetime.now(timezone.utc)` — timezone-aware
4. try/finally for lock release
5. Route to token handler if `AWAITING_TOKEN`
6. `db.get_user(user_id)`:
   - DB exception → log ERROR, send `db_error` in `language.detect(text)`,
     return
   - None → send `unregistered` in `language.detect(text)`, return
7. Handle `AWAITING_RESET` (user loaded — stored language available):
   - `"confirm"` (stripped, case-insensitive) → `db.clear_user_history`.
     Count > 0: `reset_confirmed`. Count == 0: `reset_confirmed_empty`.
     State `NORMAL`, return
   - Other → `reset_cancelled`, state `NORMAL`, return
8. `language.resolve(user["language"], user_text)` → `(lang, changed)`
9. `changed`: call `db.update_language`. Not changed: call `db.touch_user`
10. Accidental token (40-char alphanumeric) → delete, `token_accidental`,
    return
11. `crypto.decrypt_token`. ValueError → `decrypt_error`, return
12. Start keep-typing task
13. `llm.process_message(user_id, user_text, token, lang, turn_start)`
14. Cancel keep-typing (always, in finally or explicit cancel)
15. `_send_with_truncation(chat_id, response, lang)`

**`_send_with_truncation(chat_id, text, lang) → None`**
Split priority: last `\n\n` before 4096, last `\n` before 4096, hard cut
at 4096. Per chunk: `bot.send_message` in try/except. On failure: call
`bot.send_message` directly with `send_error` (never `_send_with_truncation`
recursively). Both fail: log stdout only.

**Token input handler** (lock held by caller):
1. Language: `context.user_data.get("detected_language")`. If None:
   `language.detect` on available text, else `"en"`. Overwriting on each
   `/start`/`/token` intentional. Cleared only on success.
2. Delete token message. Failure → `token_deletion_failed`, continue
3. Validate (`GET /rest/v2/projects`):
   - 200 → valid
   - Non-200 → `token_invalid`, keep `AWAITING_TOKEN`, return
   - Network exception → `token_network_error`, keep `AWAITING_TOKEN`,
     return
4. Load existing user. If exists: evict old token from MCP cache
5. `crypto.encrypt_token(plain_token)`
6. `db.save_user(user_id, encrypted_token, lang)`
7. State → `NORMAL`
8. Log `new_user` with `language`
9. Clear `context.user_data["detected_language"]`
10. Send `token_accepted`

**Keep-typing:** Typing action every 4s. Cancelled after step 13 (always).
`asyncio.CancelledError` caught on cancel.

**Heartbeat:** Sleep 60s before first event. Then every 60s: log `heartbeat`
at INFO with `status: "alive"` and `uptime_seconds` (seconds since timestamp
at top of `main()` before initialisation). Log failure does not terminate
task.

**Daily cleanup:** `datetime.now(timezone.utc)` — never local time. Sleep
until 3:00 UTC. DB calls in try/except: success → `daily_cleanup` INFO;
failure → ERROR, continue. Sleep 60s after run. Never terminates on error.

---

## Command Handlers

All handlers: private chat guard → acquire lock (contention → `please_wait`)
→ try/finally. All strings from `messages.py`.

| Command | Behaviour |
|---|---|
| `/start` | Detect language, store in `context.user_data["detected_language"]`. Load user. Exists: `already_registered` in stored lang. None: `welcome` in detected lang, state `AWAITING_TOKEN` |
| `/token` | Detect language, store. Send `welcome`. State `AWAITING_TOKEN` |
| `/reset` | Load user. None: `unregistered`, return. `reset_prompt` in stored lang. State `AWAITING_RESET` |
| `/refresh` | Load user. None: `unregistered`, return. Decrypt token. `mcp.evict_cache`. `refresh_confirmed` in stored lang |
| `/help` | Load user. Exists: stored lang. None: `language.detect(text)`. Send `help_text` |

---

## scripts/rotate_key.py

CLI: `--old-key`, `--new-key`. Both required. Run during bot downtime.

Batches of 50. Per batch: single DB transaction, decrypt old key, re-encrypt
new key, `update_token` for each. On batch failure: rollback batch, log
failed `user_id` with traceback, continue to next batch. Print per-batch
progress and final summary (succeeded / failed). Does not delete old key.

**Implementation deviations (maintainer-confirmed 2026-06-24):**
- **Keys come from the CLI, not `crypto.py`.** `crypto.encrypt_token` /
  `decrypt_token` are hardwired to `config.SECRET_KEY` and cannot take a key
  argument, so the script uses `cryptography.fernet.Fernet(old_key)` /
  `Fernet(new_key)` directly. This is the one sanctioned place that bypasses
  `crypto.py`.
- **No atomic per-batch transaction.** `db.update_token` acquires its own
  connection per call and exposes no shared-transaction handle, and extending
  `db.py` is out of scope for this utility. Therefore "single DB transaction
  per batch / rollback batch" is NOT implemented: a batch is a progress and
  error-isolation grouping only, each `update_token` commits on its own, and a
  failing row is logged-and-skipped (not rolled back). Acceptable for a manual
  v1 utility run during downtime; re-encryption is idempotent, so a re-run
  safely retries any rows missed by a prior failure.

---

## Database Schema

### users
| Column | Type | Notes |
|---|---|---|
| `telegram_user_id` | BIGINT | Primary key |
| `todoist_token` | TEXT | Fernet-encrypted |
| `language` | TEXT | `"en"` or `"ru"`, not null, default `"en"` |
| `created_at` | TIMESTAMPTZ | Default NOW() |
| `last_active_at` | TIMESTAMPTZ | Updated every turn |

### conversations
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL | Primary key |
| `user_id` | BIGINT | FK → users ON DELETE CASCADE |
| `role` | TEXT | user / assistant / tool — matches `content.role` |
| `content` | JSONB | Full OpenAI-format message object |
| `created_at` | TIMESTAMPTZ | Default NOW() |

### logs
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL | Primary key |
| `user_id` | BIGINT | Nullable |
| `level` | TEXT | INFO / DEBUG / WARNING / ERROR |
| `event` | TEXT | See table below |
| `data` | JSONB | Sanitized |
| `created_at` | TIMESTAMPTZ | Default NOW() |

**Log events:**

| Event | Level | Required data fields |
|---|---|---|
| `user_message` | INFO | `text`, `language` |
| `tool_call` | INFO | `tool`, `params`, `latency_ms` |
| `tool_call` (error) | ERROR | `tool`, `error` |
| `llm_call` | INFO | `stage`, `latency_ms`, `token_count` (null if absent) |
| `bot_response` | INFO | `latency_ms` |
| `rate_limit` | WARNING | `window`, `retry_after_seconds`, `retry_after_human` |
| `new_user` | INFO | `language` |
| `heartbeat` | INFO | `status`, `uptime_seconds` |
| `daily_cleanup` | INFO | `deleted_conversations`, `deleted_logs` |
| `error` | ERROR | `traceback`, `context` |

### Indexes
```
logs(event), logs(created_at), logs(user_id), logs(level), conversations(user_id)
```

---

## Migrations

**001_init.py** — all three tables. `users.language` TEXT not null default `'en'`.
**002_add_indexes.py** — all five indexes.

Alembic uses psycopg2 (sync) for migrations, asyncpg for runtime. Rewrites
`DATABASE_URL` to `postgresql+psycopg2://` for Alembic connection.

---

## Grafana

`grafana_reader` user, SSL `require`, host/port/db from `DATABASE_URL`.

**Dashboard 1 — Usage:** Active users, messages, new users, language split.
**Dashboard 2 — Tools:** Most used, calls over time, avg latency, success rate.
**Dashboard 3 — Performance:** Avg/P95 latency, token count
(`WHERE data->>'token_count' IS NOT NULL` before `::int` cast), latency trend.
**Dashboard 4 — Health:**

```sql
SELECT ROUND(100.0 * COUNT(h.created_at) / COUNT(s.minute), 2) AS uptime_percent
FROM generate_series(
  date_trunc('minute', NOW() - INTERVAL '24 hours'),
  date_trunc('minute', NOW()),
  INTERVAL '1 minute'
) AS s(minute)
LEFT JOIN logs h
  ON h.event = 'heartbeat'
  AND h.created_at >= s.minute
  AND h.created_at < s.minute + INTERVAL '1 minute'
```

Heartbeat timeline, errors, error rate, recent errors, cleanup stats,
rate limit hits by `data->>'window'`.

**Alerts:** No heartbeat 3min → down. 5+ ERRORs in 10min → high error rate.
Avg `latency_ms` > 10000 in 15min → degradation.

---

## Error Handling Rules

1. External failures → user-facing message in user's language, never silent
2. DB log failures → `log_stdout` only
3. Token decryption → `decrypt_error`, prompt `/token`
4. HTTP 429 → WARNING, rate limit message, no retry
5. LLM timeout → `llm_timeout`
6. MCP failure or `save_tool_result` failure →
   `delete_turn_tool_results`, `tool_failure`, discard partial results
7. `send_message` failure → `send_error` via direct `bot.send_message`.
   Both fail → stdout only
8. Token deletion failure → `token_deletion_failed`, continue
9. Language detection failure → `"en"` silently
10. Token validation network error → `token_network_error`, keep
    `AWAITING_TOKEN`
11. Lock contention → message: discard; commands: `please_wait`
12. `init_pool` failure → log stdout, exit non-zero
13. Daily cleanup failure → log ERROR, continue
14. Early return → user message saved, tool results cleaned up, error
    message not saved to history
15. `db.get_user` exception → log ERROR, `db_error` in detected language

---

## Dependencies

```
python-telegram-bot==21.x   — async native (v20+ required)
asyncpg                     — async PostgreSQL driver
psycopg2-binary             — sync driver for Alembic only
httpx[http2]                — async HTTP with HTTP/2 support
cryptography>=42.0.0        — Fernet encryption
alembic>=1.13.0             — migrations
sqlalchemy>=2.0.0           — required by Alembic
langdetect                  — offline EN/RU detection
python-dotenv               — load .env at import in config (no-op in prod)
pytest                      — test runner
pytest-asyncio              — async test support
pytest-cov                  — coverage reporting (min 80%, bot.py 70%)
```

---

## Deployment

Complete **One-Time Manual Setup** before first deploy.

```
alembic upgrade head && python bot.py
```

Migration failure → bot does not start → Railway reports failed deploy.

**Build:** Nixpacks · **Restart policy:** on_failure
