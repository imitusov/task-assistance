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
exception). Required vars raise `KeyError` at import if missing — never
silently defaulted.

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
