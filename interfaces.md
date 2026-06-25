# Interfaces

This file records the public surface of every completed module. It is appended
to after each module passes its tests. Read it before implementing any module.

Append one section per module, in the build order defined in
`dependency-order.md`, so the file reads top-to-bottom as the dependency chain.
Record exact signatures (name, typed parameters, return type, async, raised
exceptions, critical constraints) — never record a signature for unfinished
code.

## config.py

Loads all env vars at import time (module-level side effect, per CLAUDE.md
exception). Calls `dotenv.load_dotenv(override=False)` first, so a local `.env`
is auto-loaded without manual sourcing, while already-set vars (Railway's
injected vars, the test suite's monkeypatched vars) take precedence and a
missing `.env` is a silent no-op. Required vars raise `KeyError` at import if
missing — never silently defaulted. (Tests that reload `config` neutralize the
loader via `monkeypatch.setattr("dotenv.load_dotenv", ...)` so they depend only
on explicitly-set vars, never a developer's local `.env`.)

**Required (str, no default — `KeyError` if unset):**
- `TELEGRAM_BOT_TOKEN: str`
- `NEURALDEEP_API_KEY: str`
- `NEURALDEEP_API_URL: str`
- `DATABASE_URL: str`
- `SECRET_KEY: str`
- `LOG_LEVEL: str`

**Optional (int, parsed from env string, with defaults):**
- `MCP_TOOLS_TTL: int` — default `86400`
- `MAX_HISTORY_MESSAGES: int` — default `20`
- `CONVERSATION_RETENTION_DAYS: int` — default `7`
- `LOG_RETENTION_DAYS: int` — default `30`

**Optional (str, with defaults):**
- `LLM_MODEL: str` — default `"qwen3.6-35b-a3b"` (lowercase — NeuralDeep keys
  reject the capitalised `Qwen3.6-35b-a3b` with HTTP 401; consumed by `llm.py`)
- `MCP_SERVER_URL: str` — default `"https://ai.todoist.net/mcp"` (consumed by
  `mcp.py` as `MCP_ENDPOINT`)
- `TODOIST_BASE_URL: str` — default `"https://api.todoist.com/api/v1"` (Todoist
  REST base for token validation; `bot.py` builds `{base}/projects` from it.
  The legacy `api.todoist.net/rest/v2` host is dead/sunset — verified
  2026-06-24: `.net` does not resolve, `rest/v2` returns HTTP 410, the unified
  `api.todoist.com/api/v1/projects` returns 401 unauthenticated)

All modules must import these names from `config` — never read
`os.environ` directly.

## messages.py

Single source of truth for all user-facing strings (EN and RU) and command
descriptions. No internal dependencies.

**get(key: str, lang: str, \*\*kwargs: object) → str**
- Unknown `key` → raises `KeyError` (checked against the resolved language's
  dict, after fallback)
- Unknown `lang` → falls back to `"en"` silently
- `**kwargs` substituted into the template via `str.format(**kwargs)`
- `please_wait` is the only message sendable before lock acquisition (per
  contract note — no special handling inside `get` itself)

**Required keys (present for both `"en"` and `"ru"`):**
`welcome`, `token_accepted`, `token_invalid`, `token_network_error`,
`token_deletion_failed`, `token_accidental`, `already_registered`,
`unregistered`, `rate_limit_session` (`retry_time`), `rate_limit_week`
(`retry_time`), `llm_timeout`, `tool_failure`, `reset_prompt`,
`reset_confirmed` (`count`), `reset_confirmed_empty`, `reset_cancelled`,
`refresh_confirmed`, `group_chat_rejected`, `help_text`, `please_wait`,
`decrypt_error`, `send_error`, `db_error`.

**Constraint:** `send_error` is < 100 chars in both languages (enforced by
test, not by `get` itself — callers must not assume truncation).

No public command-description helper beyond `help_text` was required by the
contract for this module.

## language.py

`DetectorFactory.seed = 0` set at import for determinism. No internal
dependencies.

**Constants:**
- `SUPPORTED_LANGUAGES: set[str]` = `{"en", "ru"}`
- `DEFAULT_LANGUAGE: str` = `"en"`
- `MIN_DETECTION_LENGTH: int` = `10`
- `MIN_DETECTION_CONFIDENCE: float` = `0.9` (documented contract value —
  see deviation note below)

**detect(text: str) → str**
Returns `"en"` or `"ru"` only. Returns `DEFAULT_LANGUAGE` on text shorter
than `MIN_DETECTION_LENGTH`, an unsupported top result, low confidence, or
any exception. Never raises.

**resolve(stored_language: str | None, text: str) → tuple[str, bool]**
- Raises `TypeError` if `text is None` — caller must never pass `None`.
- Empty string `stored_language` treated identically to `None`.
- `None`/empty stored → `(detect(text), True)`.
- `len(text) < MIN_DETECTION_LENGTH` with a truthy stored value → `(stored, False)`
  (too short to justify a switch — stored value is preserved even if it
  differs from `detect()`'s default).
- Otherwise, detected == stored → `(stored, False)`; detected != stored →
  `(detected, True)`.
- Never raises except the documented `TypeError`.

**Deviation from literal contract (flagged and approved by user 2026-06-23):**
The contract specifies gating `detect()` on `MIN_DETECTION_CONFIDENCE = 0.9`
using langdetect's top-candidate probability. In practice, langdetect's
probability for short-but-correct supported-language text (e.g. ~0.71 for
the contract's own Russian test phrase) is regularly below 0.9, so a literal
0.9 gate would default that text to EN and fail the contract's own test
case. Resolution: `detect()` internally gates on a lower, undocumented
threshold (`_CONFIDENCE_GATE = 0.6`, private) instead of the public
`MIN_DETECTION_CONFIDENCE` constant. `MIN_DETECTION_CONFIDENCE` remains
defined at `0.9` to satisfy the contract's constant requirement but is not
used as the actual gate value in `detect()`.

## logger.py

Depends on `config` (reads `config.LOG_LEVEL`). No other internal
dependencies.

**sanitize(data: dict) → dict**
- Scans top-level keys only — does NOT recurse into nested dicts or lists
  (explicit design rule, not a limitation)
- Case-insensitive substring match on each key: keys containing `token`,
  `api_key`, `secret`, or `password` → value replaced with
  `"***REDACTED***"`
- Returns a new dict; never mutates the input
- Nested dict/list values are passed through unchanged regardless of their
  contents, even if a nested key would otherwise match

**log_stdout(level: str, event: str, user_id: int | None, data: dict) → None**
- Writes a single JSON line to stdout via `print`
- Fields: `timestamp` (ISO 8601, timezone-aware UTC via
  `datetime.now(timezone.utc).isoformat()`), `level`, `event`, `user_id`,
  `data` (run through `sanitize` first)
- `user_id` may be `None`
- Respects `config.LOG_LEVEL`: levels are ranked via
  `logging.getLevelNamesMapping()` (standard `DEBUG`/`INFO`/`WARNING`/
  `ERROR`/`CRITICAL` ranks); if `level`'s rank is below
  `config.LOG_LEVEL`'s rank, nothing is printed
- Never raises on its own — callers per the spec's Error Handling Rule 2
  (DB log failures → `log_stdout` only) rely on this being a safe fallback

## crypto.py

Fernet symmetric encryption/decryption of Todoist tokens, keyed by
`config.SECRET_KEY`. No module-level side effects — `Fernet` is instantiated
inside each function call, not cached at import time.

**encrypt_token(plain: str) → str**
- Fernet-encrypts `plain` using `config.SECRET_KEY`, returns the base64
  ciphertext as `str`
- On any failure (including non-`str` input), raises `ValueError` with a
  fixed descriptive message — never the raw `cryptography` exception, never
  includes `plain` in the message
- Output differs from `plain` and is non-deterministic across calls (random
  IV/nonce per Fernet encryption)

**decrypt_token(encrypted: str) → str**
- Decrypts a Fernet ciphertext produced by `encrypt_token`, returns the
  original plain `str`
- On any failure (invalid/corrupted ciphertext, wrong key, etc.), raises
  `ValueError` with a fixed descriptive message — never `InvalidToken` or any
  other raw `cryptography` exception, and never includes the plain token in
  the message
- Callers (`bot.py`) catch `ValueError` specifically to send `decrypt_error`

## migrations/

Alembic, `script_location = migrations` (see `alembic.ini`). `env.py` reads
`DATABASE_URL` from `config` (never `os.environ`) and rewrites the
`postgresql://` prefix to `postgresql+psycopg2://` before connecting — uses
psycopg2 only, never imports `asyncpg`. Revision ids are literal strings
`"001"` and `"002"` (`down_revision` of `002` = `"001"`). Verified with a
real `upgrade head` → `downgrade base` round-trip against a disposable local
Postgres 16 instance (not Docker — Docker was unavailable; brew-installed
Postgres used instead per user direction).

**`001_init.py`** creates:
- `users(telegram_user_id BIGINT PK [autoincrement=False — externally
  supplied Telegram id, not a sequence], todoist_token TEXT NULL,
  language TEXT NOT NULL DEFAULT 'en', created_at TIMESTAMPTZ DEFAULT NOW(),
  last_active_at TIMESTAMPTZ NULL)`
- `conversations(id SERIAL PK, user_id BIGINT NOT NULL FK → users.telegram_user_id
  ON DELETE CASCADE, role TEXT NOT NULL, content JSONB NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW())`
- `logs(id SERIAL PK, user_id BIGINT NULL, level TEXT NOT NULL,
  event TEXT NOT NULL, data JSONB NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW())`

**`002_add_indexes.py`** (`down_revision = "001"`) creates:
`ix_logs_event` on `logs(event)`, `ix_logs_created_at` on `logs(created_at)`,
`ix_logs_user_id` on `logs(user_id)`, `ix_logs_level` on `logs(level)`,
`ix_conversations_user_id` on `conversations(user_id)`.

**Constraint verified by round-trip:** `users.language` NOT NULL DEFAULT
`'en'`; `conversations.user_id` FK is `ON DELETE CASCADE` (confirmed:
deleting a user row deletes its conversation rows); all timestamp columns
are `TIMESTAMPTZ`, never naive; `downgrade base` leaves only Alembic's own
`alembic_version` table — no leftover application tables or indexes.

`db.py` (module 7) and `tests/test_db.py` can rely on this exact shape —
no other schema exists.

## db.py

Single data-access layer; all SQL lives here. Depends on `config`
(`DATABASE_URL`, `MAX_HISTORY_MESSAGES`) and `logger` (`sanitize`,
`log_stdout`) — calls them via `import config` / `import logger` (module
attribute access, not `from x import y`), so tests can `patch("db.logger.log_stdout")`
and `patch("db.asyncpg.create_pool")`. Module-level `_pool: asyncpg.Pool | None`,
`None` until `init_pool()` runs.

**Pool lifecycle**
- `init_pool() -> None` (async) — `asyncpg.create_pool(dsn=config.DATABASE_URL,
  min_size=2, max_size=10)`. Raises whatever `asyncpg.create_pool` raises —
  does not catch or wrap. Caller (per Rule 12) must catch, log, exit non-zero.
- `close_pool() -> None` (async) — closes `_pool` and resets it to `None`.
  Safe to call when `_pool` is already `None` (no-op).

**Users**
- `get_user(user_id: int) -> dict | None` — `dict` keys: `telegram_user_id`,
  `todoist_token`, `language`, `created_at`, `last_active_at`. `None` if no row.
- `save_user(user_id: int, encrypted_token: str, language: str) -> None` —
  upsert on `telegram_user_id`; conflict path updates `todoist_token`,
  `language`, `last_active_at` (`NOW()`); `created_at` only set on insert.
- `update_language(user_id: int, language: str) -> None` — updates `language`
  and `last_active_at`; leaves `todoist_token` untouched.
- `touch_user(user_id: int) -> None` — updates only `last_active_at`.
- `get_all_users() -> list[dict]` — same row shape as `get_user`, all rows.
  Key-rotation script only.
- `update_token(user_id: int, encrypted_token: str) -> None` — updates only
  `todoist_token`. Key-rotation script only.

**Conversations** (`content`/`data` stored as JSONB via explicit
`json.dumps(...)::jsonb` casts on write and `json.loads(...)` on read — no
asyncpg jsonb codec is registered, so this is independent of how a
connection was created; test fixtures don't need extra codec setup)
- `get_history(user_id: int) -> list[dict]` — `content` objects only,
  `created_at ASC, id ASC` order, excludes `role = "system"`. `[]` if none.
- `save_user_message(user_id: int, content: dict) -> None` — inserts a row
  with `role = content["role"]`. Never trims.
- `save_turn(user_id: int, assistant_content: dict) -> None` — inserts a row
  with `role = assistant_content["role"]`, then inside the **same**
  `conn.transaction()` deletes all but the most recent
  `config.MAX_HISTORY_MESSAGES` rows for that `user_id` (tie-broken by `id`
  for determinism when `created_at` collides — Postgres freezes `NOW()` for
  the duration of a transaction, so rapid inserts inside one transaction can
  share a timestamp). The only place trimming happens.
- `save_tool_result(user_id: int, tool_content: dict) -> None` — inserts a
  row with `role = tool_content["role"]`. Never trims.
- `delete_turn_tool_results(user_id: int, since: datetime) -> None` —
  deletes `role = "tool"` rows where `created_at >= since` (inclusive by
  design). Safe with zero matching rows.
  **Deviation from literal contract (flagged, no blocking ambiguity —
  documented here per the project's established deviation-handling
  precedent, see [[language.py]]):** the contract says "asyncpg rejects
  naive datetimes for TIMESTAMPTZ." Verified empirically against asyncpg
  0.31.0 — it does **not** reject them; a naive `datetime` is silently
  interpreted as local time and inserted without error. To satisfy the
  contract's required *behavior* ("passing naive datetime raises"),
  `delete_turn_tool_results` explicitly raises `ValueError` itself when
  `since.tzinfo is None`, rather than relying on asyncpg to do it.
- `clear_user_history(user_id: int) -> int` — deletes all roles for the
  user, returns the deleted row count (`0` if none existed).
- `cleanup_old_conversations(days: int) -> int` — `WHERE created_at < NOW() -
  make_interval(days => $1)`, integer bind param. Returns rows deleted.
- `cleanup_old_logs(days: int) -> int` — same pattern over `logs`.

**Logging**
- `log(user_id: int | None, level: str, event: str, data: dict) -> None` —
  sanitizes `data` via `logger.sanitize` before inserting into `logs`; the
  insert is wrapped in `try/except Exception: pass` (Rule 2 — DB log
  failures never propagate). `logger.log_stdout(level, event, user_id, data)`
  is then called **unconditionally** (success or failure) with the
  *original*, unsanitized `data` — `log_stdout` does its own sanitization
  internally per its own contract, so this is not a double-redaction bug.

**Content structure** (`role` column always equals `content["role"]`,
enforced by callers, not by `db.py`):
- user: `{"role": "user", "content": "string"}`
- assistant (text): `{"role": "assistant", "content": "string"}`
- assistant (tool call): `{"role": "assistant", "content": null, "tool_calls": [...]}`
- tool: `{"role": "tool", "tool_call_id": "string", "content": "string"}`

**Test infra note for future modules' tests:** `tests/test_db.py` requires
`TEST_DATABASE_URL` (real Postgres, never production) and skips entirely if
unset. A session-scoped autouse fixture runs `alembic upgrade head` /
`downgrade base` once for the whole session. Each test gets its own
`asyncpg.connect()` wrapped in a manually-started transaction that is
rolled back in teardown; `db._pool` is monkeypatched to a tiny
`_SingleConnPool` wrapper whose `acquire()` yields that same connection
(no real pool, no release/commit) so all queries in a test stay inside the
one rolled-back transaction.

## mcp.py

Client for the official Todoist MCP server. The endpoint is module constant
`MCP_ENDPOINT = config.MCP_SERVER_URL` (default `https://ai.todoist.net/mcp`,
read once at import). Depends only on `config` (`MCP_SERVER_URL` at import;
`MCP_TOOLS_TTL` read fresh on every `get_tools` call, not cached at import) — does **not**
import `db.py` or `logger.py` (the contract listed `logger` as available
but the module ended up not needing it: no failure path here is swallowed
or logged internally — every failure is a raised exception for the caller,
typically `llm.py`, to log and translate into a user-facing message per
Rule 1/Rule 6). Pure in-process, per-token cache — no module-level side
effects beyond the `_cache` dict and `_TIMEOUT` constant.

**Public**
- `get_tools(token: str) -> list` (async) — OpenAI-format tool list
  (`[{"type": "function", "function": {...}}, ...]`), cached per `token` in
  module-level `_cache: dict[str, tuple[list, float]]` keyed by raw token
  string. Cache hit requires `time.time() - fetched_at < config.MCP_TOOLS_TTL`;
  otherwise re-fetches via JSON-RPC `tools/list`, converts every tool, caches
  the **converted** list (never raw MCP shape), and returns it.
- `call_tool(token: str, name: str, arguments: dict) -> dict` (async) —
  JSON-RPC `tools/call` with `params={"name": name, "arguments": arguments}`;
  returns the `result` field of the parsed response (a `dict`, e.g.
  `{"content": [...], "isError": false}` per the MCP tool-call result
  shape). Raises (never swallows) on HTTP error status
  (`response.raise_for_status()`), SSE parse failure, a JSON-RPC `error`
  field in the response (`RuntimeError`), or `httpx` timeout.
- `evict_cache(token: str) -> None` — `_cache.pop(token, None)`; silent
  no-op if the token was never cached.

**Private helpers** (not part of the module's public contract surface, but
directly unit-tested since there's no public wrapper purely for them):
- `_parse_sse(text: str) -> dict` — scans lines (each stripped) for the
  first one starting with `"data:"`, strips the prefix and surrounding
  whitespace, `json.loads`s it. Raises `ValueError` containing the raw
  `text` verbatim (not `repr`'d) if no `data:` line exists.
- `_convert_tool(tool: dict) -> dict` — copies every key except
  `inputSchema` into `function`, adds `function["parameters"] =
  tool.get("inputSchema", {})`, wraps as `{"type": "function", "function":
  function}`. Empty `inputSchema` → empty `parameters`. All non-schema
  fields (`name`, `description`, anything else MCP sends) pass through
  unchanged.
- `_call_rpc(token, method, params=None) -> dict` — shared JSON-RPC/SSE
  request plumbing for both public functions; builds the
  `{"jsonrpc": "2.0", "id": 1, "method": ..., "params": ...}` envelope,
  POSTs to `MCP_ENDPOINT` with `Authorization: Bearer <token>`,
  `Content-Type: application/json`, `Accept: application/json,
  text/event-stream`, under `httpx.AsyncClient(timeout=_TIMEOUT)`.
- `_headers(token: str) -> dict` — the three headers above.

**Deviation from literal contract (flagged, no blocking ambiguity —
same documented-deviation precedent as [[language.py]] and the
`delete_turn_tool_results` note in [[db.py]]):** the contract specifies
`httpx.Timeout(total=10.0)`. The installed `httpx` (0.28.1) does not accept
a `total` keyword on `Timeout` (`TypeError: unexpected keyword argument
'total'` — verified empirically). `_TIMEOUT = httpx.Timeout(10.0)`
(positional) is used instead, which sets connect/read/write/pool all to
10 seconds — the same intent ("10s total for full SSE receipt") expressed
in the form this `httpx` version actually supports.

## llm.py

Orchestration core. Returns plain answer strings only — no Telegram
knowledge. Calls `db` and `mcp` via plain module attribute access
(`import db` / `import mcp`) so tests can overwrite `llm.db.*` / `llm.mcp.*`
wholesale with mocks (no real Postgres or network needed for
`tests/test_llm.py` at all). Also imports `config` and `messages`. Does
**not** import `logger` directly — all of `llm.py`'s structured logging
(`llm_call`, `tool_call`, `rate_limit`, `bot_response`, and tool-failure
`ERROR`s) goes through `db.log(user_id, level, event, data)`, which
persists to the Postgres `logs` table (and internally calls
`logger.sanitize` + `logger.log_stdout`) — required so these events are
queryable by the Grafana dashboards described in the spec's Log Events
table. **Flagged resolution (user-confirmed 2026-06-23, no blocking
ambiguity left):** this task's own interface list named only
`logger.log_stdout` as available and omitted `db.log` from the `db.py`
function list it gave — unlike every other `db.py` function this module
uses — which conflicted with the technical spec's Log Events table (which
maps these exact event names onto the `logs` table) and with `db.log`
already being implemented/tested in [[db.py]]. Resolved in favor of
`db.log` so events actually reach Grafana; treated the task file's
omission as an oversight.

**Public**
- `process_message(user_id: int, user_text: str, token: str, language: str, turn_start: datetime) -> str`
  (async) — `turn_start` must be the exact timezone-aware `datetime` the
  caller (`bot.py`) captured after lock acquisition; every cleanup call
  inside this function passes that same object through unchanged (never a
  freshly generated timestamp) — verified by identity (`is`), not just
  equality, in `tests/test_llm.py`. Step order matches the contract exactly:
  `db.get_history` → build system message → build user message → ALWAYS
  `db.save_user_message` (before any LLM call, including all early-return
  paths) → `mcp.get_tools` → first LLM call (`tool_choice="auto"`, 30s
  timeout) → no-tool-calls path (`db.save_turn` once, return answer) or
  tool-calls path (execute every tool call via `mcp.call_tool` +
  `db.save_tool_result`, second LLM call, `db.save_turn` once, return
  answer). On `mcp.call_tool` failure or `db.save_tool_result` failure:
  logs `tool_call` at ERROR, calls `db.delete_turn_tool_results(user_id,
  turn_start)`, returns `messages.get("tool_failure", language)` —
  remaining tool calls in that turn are skipped, `db.save_turn` is never
  reached. On HTTP 429 from either LLM call: logs `rate_limit` at WARNING
  (never ERROR) and returns `rate_limit_session`/`rate_limit_week`
  (selected by the `X-Window` response header; anything other than the
  literal string `"week"` is treated as `"session"`); the *second*-call 429
  path additionally calls `delete_turn_tool_results` first (the first-call
  429 path does not, since no tool results exist yet to clean up). On
  `httpx.TimeoutException` from either LLM call: returns `llm_timeout`
  (logged at WARNING via the `llm_call` event with `error: "timeout"`); the
  second-call path additionally calls `delete_turn_tool_results` first.
  `db.save_user_message` always happens; the error message itself is never
  saved to history (Rule 14).

**Private helpers** (no public wrapper needed solely for these, so they're
unit-tested directly):
- `_build_system_message(language: str) -> dict` — `{"role": "system",
  "content": ...}`. Template selected from module-level `_SYSTEM_PROMPTS`
  (`"en"`/`"ru"`, **not** in `messages.py` — the brief's file tree
  explicitly assigns system-prompt ownership to `llm.py`, and these strings
  are never shown to the user or stored, so `messages.py`'s
  user-facing-string rule doesn't apply). Unknown language silently falls
  back to `"en"`, consistent with `messages.get`'s own fallback. Date comes
  from `_today_str()`.
- `_today_str() -> str` — `datetime.now(timezone.utc).strftime("%Y-%m-%d")`,
  factored out specifically so tests can `monkeypatch.setattr(llm,
  "_today_str", lambda: "...")` instead of subclassing `datetime`.
- `_strip_thinking(text: str | None) -> str | None` — depth-counting scan
  that removes everything between `<think>` and `</think>`, including
  nested blocks (tracks `depth`, skips characters while `depth > 0`) and
  incomplete/unclosed blocks (an opening tag with no matching close
  consumes the rest of the string, since nothing reliable follows
  half-finished reasoning). A stray `</think>` with no prior `<think>` is
  silently dropped without raising `depth` below zero. Passes `None`
  through unchanged (needed because `content` is `null` on the
  tool-calling turn of a response). **Always active** — `test_qwen_tools.py`
  was never run/recorded in this repo to confirm Qwen actually emits
  `<think>` blocks (the contract gates this feature on that script's
  result), and the user confirmed (2026-06-23) to implement it
  unconditionally rather than skip it; safe no-op on plain text.
- `_format_retry_time(seconds: int) -> str` — turns a `Retry-After` second
  count into a compact human string (`"1d 1h"`, `"45s"`, etc.) for the
  `retry_time` kwarg of `rate_limit_session`/`rate_limit_week`. Not
  specified by the contract beyond "the value appears in the message" —
  this exact format is this implementation's own choice.
- `_extract_token_count(payload: dict) -> int | None` — `payload["usage"]
  ["total_tokens"]` if `usage` is present and truthy, else `None`.
- `_call_llm(messages_payload: list[dict], tools: list, user_id: int) -> tuple[httpx.Response, float]`
  (async) — POSTs to `f"{config.NEURALDEEP_API_URL}/chat/completions"`
  with `{"model": _MODEL, "messages": ..., "tools": ..., "tool_choice":
  "auto", "user": str(user_id)}` under `httpx.AsyncClient(timeout=_LLM_TIMEOUT)`
  (30s, plain
  `httpx.Timeout(30.0)` positional form — see the [[mcp.py]] deviation
  note on why `total=` isn't used here either). Returns the raw
  `httpx.Response` (not pre-parsed JSON) plus latency in milliseconds, so
  the caller can branch on `status_code == 429` before ever calling
  `.json()`. The `"user": str(user_id)` field is a NeuralDeep performance
  hint, not abuse-tracking: a stable per-user id makes the balancer route the
  user to the same upstream, keeping the prompt KV-cache warm across turns
  (faster multi-turn). `user_id` is threaded from `process_message` →
  `_run_llm_call` → `_call_llm`. Module constant `_MODEL = config.LLM_MODEL`
  (default `"qwen3.6-35b-a3b"`, read once at import). Note: the name is
  lowercase — the capitalised `Qwen3.6-35b-a3b` from the brief's Tech Stack
  table is rejected by NeuralDeep keys with HTTP 401 (verified via
  `test_qwen_tools.py`); overridable via the `LLM_MODEL` env var.
- `_handle_rate_limit(user_id: int, response: httpx.Response, language: str) -> str`
  (async) — shared by both the first- and second-call 429 branches.
- `_run_llm_call(stage, messages_payload, tools, user_id, language, turn_start, cleanup_on_failure) -> tuple[dict | None, str | None]`
  (async) — added during a post-implementation cleanup pass (2026-06-24) to
  deduplicate `process_message`'s two near-identical LLM-call blocks
  (timeout handling, 429 handling, success logging were repeated almost
  verbatim for the `"tool_selection"` and `"answer_generation"` stages).
  Wraps one `_call_llm` call: on success returns `(message, None)`; on
  timeout or 429 returns `(None, early_return_answer)` after logging.
  `cleanup_on_failure` is `False` for the first call (nothing to clean up
  yet) and `True` for the second (calls `db.delete_turn_tool_results(
  user_id, turn_start)` before either failure return) — this flag is the
  one behavioral difference the contract draws between the two calls'
  failure paths, so it's threaded through explicitly rather than inferred.

## bot.py

Entry point and Telegram layer. Calls `db`, `mcp`, `llm` via plain module
attribute access (`import db` / `import mcp` / `import llm`) so tests
overwrite `bot.db.*` / `bot.mcp.*` / `bot.llm.*` wholesale with mocks — no
real Postgres, MCP, or LLM network calls needed for `tests/test_bot.py` at
all (only the Todoist token-validation HTTP call is separately mocked via
`bot.httpx.AsyncClient`). Also imports `config`, `crypto`, `language`,
`logger`, `messages`. Standardizes on `context.bot.send_message(chat_id=...,
text=...)` for every outgoing message in every handler (never
`update.message.reply_text(...)`) — deliberate, since the token-input
handler deletes the user's message before responding, and replying to an
already-deleted message is unreliable; using direct `send_message`
everywhere keeps the pattern uniform across the whole module.

**State constants:** `STATE_NORMAL = "NORMAL"`, `STATE_AWAITING_TOKEN =
"AWAITING_TOKEN"`, `STATE_AWAITING_RESET = "AWAITING_RESET"`. Stored in
`context.user_data["state"]` (absent == `STATE_NORMAL`). Per-user lock in
`context.user_data["lock"]` (`_get_lock(context)` —
`context.user_data.setdefault("lock", asyncio.Lock())`).

**Handlers** (all: `_reject_if_group` first, then lock-contention check,
then `await lock.acquire()` / `try` / `finally: lock.release()`):
- `message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None`
  (async) — registered against `filters.TEXT & ~filters.COMMAND`. Lock
  contention → discard silently (no message sent), unlike the command
  handlers. Captures `turn_start = datetime.now(timezone.utc)` immediately
  after acquiring the lock, before any other work, and passes that exact
  object through to `llm.process_message` unchanged. Delegates the bulk of
  the step-by-step contract to the private `_process_message` so the
  lock/`finally` wrapper stays trivial and easy to verify independently of
  the business logic inside it.
- `start_command`, `token_command`, `reset_command`, `refresh_command`,
  `help_command` — same `(update, context) -> None` signature. Lock
  contention → `messages.get("please_wait", context.user_data.get(
  "detected_language", "en"))` (the only language source available before
  any `db.get_user` call has happened). Behavior matches the Command
  Handlers table in the technical spec exactly (see task file for the
  per-command table; not duplicated here to avoid drift — this module's
  docstring-equivalent is the function bodies themselves, which are short).

**Private helpers:**
- `_is_private(update) -> bool` — `update.effective_chat.type == "private"`.
- `_reject_if_group(update, context) -> bool` (async) — sends
  `group_chat_rejected` (always in `"en"` — no user/language context exists
  yet at this point) and returns `True` if not private; callers `return` on
  `True`.
- `_looks_like_token(text: str) -> bool` — `^[a-zA-Z0-9]{40}$` on
  `text.strip()`. Heuristic for "user pasted their token without /token
  first," not the actual Todoist validation (that's `_handle_token_input`'s
  job).
- `_process_message(update, context, turn_start)` (async) — the step
  5-15 body of the message handler contract, run while the lock from
  `message_handler` is already held. Routes to `_handle_token_input` if
  state is `AWAITING_TOKEN`; otherwise: `db.get_user` (exception → `db_error`
  in `language.detect(text)`; `None` → `unregistered` in
  `language.detect(text)`) → `AWAITING_RESET` branch (stored language,
  `"confirm"` case-insensitive/stripped → `db.clear_user_history`,
  `reset_confirmed`/`reset_confirmed_empty` by count, else
  `reset_cancelled`; state → `NORMAL`; return either way) →
  `language.resolve` (+ `update_language` or `touch_user`) → accidental-token
  check (deletes the message, swallowing any deletion error silently — no
  `token_deletion_failed` here, that message is specific to
  `_handle_token_input`'s own deletion step) → `crypto.decrypt_token`
  (`ValueError` → `decrypt_error`) → keep-typing task → `llm.process_message`
  → `_cancel_keep_typing` (always, via `finally`) → `_send_with_truncation`.
- `_handle_token_input(update, context)` (async) — lock already held by
  the caller (`message_handler`/`_process_message`), does not touch the
  lock itself. Language: `context.user_data.get("detected_language")`,
  falling back to `language.detect(text)` if absent (never an explicit
  `"en"` branch — `language.detect` already defaults there on its own).
  Deletes the token message first (failure → sends
  `token_deletion_failed` but **continues**, does not return). Validates
  via `GET {config.TODOIST_BASE_URL}/projects` (default
  `https://api.todoist.com/api/v1/projects`; module constant
  `_TODOIST_PROJECTS_URL`) with `Authorization: Bearer <plain_token>`
  (10s timeout): network exception
  (`httpx.HTTPError`) → `token_network_error`, state untouched (stays
  `AWAITING_TOKEN`); non-200 → `token_invalid`, state untouched. On success:
  loads the existing user (if any) and evicts its **old** plaintext token
  from the MCP cache (`crypto.decrypt_token` + `mcp.evict_cache`, swallowing
  `ValueError` if the stored ciphertext is somehow corrupt) *before*
  encrypting and saving the new one — `mcp.evict_cache` is always called
  with the OLD token, never the new one. Then `crypto.encrypt_token` →
  `db.save_user` → state → `NORMAL` → `db.log(user_id, "INFO", "new_user",
  {"language": lang})` → `context.user_data.pop("detected_language", None)`
  → `token_accepted`.
- `_send_or_wait(update, context) -> bool` (async) — shared lock-contention
  check for all five command handlers; sends `please_wait` and returns
  `True` if contended (caller returns immediately), else returns `False`
  without touching the lock (the caller acquires it right after).
- `_command_guard(update, context) -> bool` (async) — added during a
  post-implementation cleanup pass (2026-06-24): combines `_reject_if_group`
  + `_send_or_wait` into the one preamble line every command handler needs
  (`if await _command_guard(update, context): return`). `message_handler`
  does **not** use this — its lock-contention behavior (discard silently,
  no `please_wait`) and `turn_start` capture point differ, so it keeps its
  own explicit group-check + `lock.locked()` check.
- `_locked(context)` (async context manager, via `@asynccontextmanager`) —
  added in the same cleanup pass to replace the repeated `await
  lock.acquire(); try: ...; finally: lock.release()` block in
  `message_handler` and all five command handlers with `async with
  _locked(context): ...`. Behaviorally identical (same lock object via
  `_get_lock`, same acquire-then-release-in-finally semantics) — purely a
  duplication removal, verified by the full `tests/test_bot.py` suite
  passing unchanged.
- `_split_into_chunks(text: str, limit: int = 4096) -> list[str]` — pure,
  synchronous. Greedily finds the rightmost `\n\n` at or before `limit` in
  the remaining text; if none, the rightmost single `\n`; if neither, hard
  `limit`-char cut. The matched separator is consumed (not duplicated at
  the start of the next chunk or left dangling at the end of the current
  one). Repeats until what's left fits in one chunk.
- `_send_with_truncation(telegram_bot, chat_id: int, text: str, lang: str) -> None`
  (async) — note the signature takes `telegram_bot` as an explicit first
  parameter (the contract's prose listed only `(chat_id, text, lang)`, but
  the function obviously needs a bot instance to call `send_message` on; a
  module-level global bot reference was considered and rejected as worse
  for testability — callers pass `context.bot` explicitly instead). Sends
  each chunk from `_split_into_chunks` via `telegram_bot.send_message`; on
  the first chunk-send failure, attempts one direct `send_message` with
  `messages.get("send_error", lang)` and then **stops** (does not attempt
  remaining chunks); if that fallback also fails, logs via
  `logger.log_stdout("ERROR", "send_error", None, {"chat_id": chat_id})`
  and returns — never raises.
- `_keep_typing(telegram_bot, chat_id: int) -> None` (async) — infinite loop:
  `send_chat_action(TYPING)` then `asyncio.sleep(4)`. No internal
  `try`/`except` for `asyncio.CancelledError` — cancellation during either
  `await` naturally propagates out and marks the task cancelled, which is
  exactly what `_cancel_keep_typing`'s `await task` is waiting to observe;
  an earlier version caught-and-re-raised `CancelledError` here, which was
  a no-op removed in a 2026-06-24 cleanup pass (behaviorally identical,
  confirmed by `tests/test_bot.py::test_keep_typing_sends_typing_action_until_cancelled`
  still passing unchanged).
- `_cancel_keep_typing(task: asyncio.Task | None) -> None` (async) —
  no-op if `task` is `None`; otherwise `task.cancel()` then `await task`
  inside a `try`/`except asyncio.CancelledError: pass`, so callers never
  need their own try/except around this.

**Background tasks:**
- `_heartbeat(start_time: float) -> None` (async) — infinite loop:
  `asyncio.sleep(60)` **before** the first event (so the first heartbeat
  log happens 60s after the task starts, not immediately), then
  `db.log(None, "INFO", "heartbeat", {"status": "alive", "uptime_seconds":
  int(time.monotonic() - start_time)})` wrapped in its own
  `try/except Exception: pass` — a log failure never terminates the loop.
  `start_time` is `time.monotonic()` captured at the very top of `main()`,
  threaded through `application.bot_data["start_time"]` (set before
  `post_init` runs) rather than a closure, so it's available inside
  `_post_init` without any global state.
- `_seconds_until_next_cleanup(now: datetime) -> float` — pure helper:
  next `03:00 UTC` at or after `now` (today's 03:00 if still in the future,
  else tomorrow's).
- `_daily_cleanup() -> None` (async) — infinite loop: compute sleep via
  `_seconds_until_next_cleanup(datetime.now(timezone.utc))`, sleep, then run
  `db.cleanup_old_conversations(config.CONVERSATION_RETENTION_DAYS)` +
  `db.cleanup_old_logs(config.LOG_RETENTION_DAYS)` inside one
  `try/except Exception`: success → one `db.log(None, "INFO",
  "daily_cleanup", {"deleted_conversations": ..., "deleted_logs": ...})`;
  failure → one `db.log(None, "ERROR", "daily_cleanup", {"error": str(exc)})`
  — **same event name** at a different level, per the contract's literal
  "success → daily_cleanup INFO; failure → ERROR" phrasing (not a separate
  generic `"error"` event for this particular task). Always
  `asyncio.sleep(60)` after a run, success or failure, before recomputing
  the next 03:00 target.

**Startup/shutdown (`main()`):**
- `main() -> None` (sync) — captures `start_time = time.monotonic()` first,
  builds the `Application` with `.post_init(_post_init)` and
  `.post_shutdown(_post_shutdown)`, stores `start_time` into
  `application.bot_data["start_time"]`, registers the five
  `CommandHandler`s and the text `MessageHandler`, then
  `application.run_polling()`. SIGTERM/SIGINT registration is **not** done
  manually — `Application.run_polling()` already installs signal handlers
  for graceful shutdown by default in `python-telegram-bot` 21.x; adding a
  second manual registration would risk conflicting with it.
- `_post_init(application: Application) -> None` (async) — calls
  `db.init_pool()`; on failure, logs via `logger.log_stdout("ERROR",
  "error", None, {...})` and **re-raises** rather than calling
  `sys.exit`/`os._exit` directly. An uncaught exception during PTB startup
  aborts `run_polling()` and terminates the process with a non-zero exit
  code on its own, which satisfies Rule 12 ("log stdout, exit non-zero")
  without fragile process-termination calls inside an async callback. On
  success, starts `_heartbeat`/`_daily_cleanup` as tasks stored in
  `application.bot_data["heartbeat_task"]`/`["cleanup_task"]`.
- `_post_shutdown(application: Application) -> None` (async) — cancels
  both background tasks, then `await db.close_pool()` in a `finally` (PTB
  itself handles "stop polling" as part of its own shutdown sequence before
  calling this hook).

**Test infra note for future modules' tests (none remain — this is module
10 of 11, only `scripts/rotate_key.py` is left):** `tests/test_bot.py`
needs a *valid* Fernet key for `SECRET_KEY` (not an arbitrary string) since
`_make_user`'s fixture round-trips real `crypto.encrypt_token`/
`decrypt_token` rather than mocking `crypto` — this caught a real bug
class early (a plausible-looking but invalid key silently breaking every
test that reaches the decrypt step) that a mocked `crypto` would have
hidden. Background-task tests (`_heartbeat`, `_daily_cleanup`) drive the
infinite loop via a fake `asyncio.sleep` that raises `CancelledError` after
N calls to terminate it deterministically — note that patching
`bot.asyncio.sleep` patches the *same* `asyncio` module object the test
file itself imported (there's only one `asyncio` module per process), so a
test's own `await asyncio.sleep(...)` calls get redirected too; prefer
patching a small `_KEEP_TYPING_INTERVAL_SECONDS`-style module constant
instead of `asyncio.sleep` itself when the test also needs real scheduling
yields (as `_keep_typing`'s test does). `_patch_token_validation(bot_module,
monkeypatch, status_code=200, get_side_effect=None) -> AsyncMock` (added in
a 2026-06-24 cleanup pass) replaces the 5-line `httpx.AsyncClient` mocking
block that was previously duplicated across ~12 token-input tests; returns
the mock client instance so a test can still reconfigure `client_instance
.get` mid-test for sequence tests (e.g. invalid token, then retry valid).

## scripts/rotate_key.py

Offline operator CLI, run during bot downtime — module 11 of 11, the last.
Calls `db` via plain module attribute access (`import db`) so tests
overwrite `rotate_key.db.*` wholesale with mocks (no real Postgres needed).
Does **not** import `logger.py` or `crypto.py` — deliberate, not an
oversight: `crypto.py`'s functions are hardwired to `config.SECRET_KEY` and
can't take the CLI's `--old-key`/`--new-key`, so this is the one sanctioned
place in the project that calls `cryptography.fernet.Fernet` directly; and
this script's output is plain `print()` (per-batch progress, final summary,
failure lines), not the structured JSON `logger.log_stdout`/`db.log`
pipeline the live bot uses — there's no Grafana-facing reason for an
offline rotation run to emit structured log events.

**Two contract deviations, maintainer-confirmed 2026-06-24 (see the task
file's "RESOLVED" section for the full reasoning — not re-litigated here):**
1. Keys come from the CLI via direct `Fernet(old_key.encode())` /
   `Fernet(new_key.encode())`, never via `crypto.encrypt_token`/
   `decrypt_token`.
2. No atomic "single transaction per batch with rollback" — `db.update_token`
   has no shared-transaction handle and extending `db.py` was out of scope.
   A "batch" is a progress/error-isolation grouping only: each
   `update_token` call commits independently, and a failing row is
   logged-and-skipped, not rolled back together with its batch.

**Public**
- `main(argv: list[str] | None = None) -> None` (sync) — parses args via
  `_parse_args`, then `asyncio.run(rotate(args.old_key, args.new_key))`.
  Entry point for `python -m scripts.rotate_key --old-key ... --new-key ...`
  (also runnable as a script via the `if __name__ == "__main__":` guard).
  Missing `--old-key`/`--new-key` → `argparse` prints a usage error and
  raises `SystemExit` with a non-zero code on its own (no custom handling
  needed). **Test note:** `main()` must be called from a *synchronous* test
  — it calls `asyncio.run()` internally, which raises `RuntimeError` if
  invoked from inside an already-running event loop (e.g. from an
  `async def` test under `pytest-asyncio`).
- `rotate(old_key: str, new_key: str) -> tuple[int, int]` (async) — the
  actual rotation entry point tests call directly (bypassing argparse).
  Returns `(total_succeeded, total_failed)`. Sequence: `db.init_pool()` →
  (inside `try`) `db.get_all_users()` → split into `BATCH_SIZE`-sized
  (`50`) chunks → `_process_batch` per chunk, printing one `"Batch
  i/n: X succeeded, Y failed"` line after each → (inside `finally`)
  `db.close_pool()` — called even if `get_all_users()` itself raises (that
  exception still propagates after cleanup; it isn't a "per-user" failure
  the contract asks to isolate). Prints a final `"Done. Succeeded: X,
  Failed: Y"` line after the loop. Empty user list → zero batches, no
  `"Batch"` lines, summary still prints `0`/`0`.

**Private helpers:**
- `_parse_args(argv: list[str] | None = None) -> argparse.Namespace` —
  `--old-key`/`--new-key`, both `required=True`.
- `_rotate_token(encrypted_old: str, old_fernet: Fernet, new_fernet: Fernet) -> str`
  — decrypt under `old_fernet`, re-encrypt under `new_fernet`. Raises
  whatever `Fernet.decrypt`/`encrypt` raises (e.g. `InvalidToken`) on bad
  input — caught by the caller, not here.
- `_process_batch(batch: list[dict], old_fernet: Fernet, new_fernet: Fernet) -> tuple[int, int]`
  (async) — per user in the batch: `_rotate_token` + `db.update_token`
  wrapped in one broad `try/except Exception` (intentional — this is the
  one place in the project where a blanket catch-and-continue is the
  contract-mandated behavior, not a violation of the "don't catch silently"
  rule, since every failure is printed with the `user_id` and a full
  traceback via `traceback.print_exc()` before moving on). Never raises;
  returns `(succeeded, failed)` counts for that batch. Never prints
  `user["todoist_token"]` or either key — only `user_id` and the caught
  exception's own traceback, which Fernet's exceptions don't populate with
  key/token material.
- `BATCH_SIZE = 50` (module constant).
