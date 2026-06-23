# Task: Implement `bot.py`

## Product context
This is a personal Telegram bot that lets multiple independent users manage and
query their own Todoist tasks through natural conversation, powered by an open
LLM (Qwen) with tool calling, hosted on Railway with observability via Grafana.
This module is the **entry point and Telegram layer**: it owns all chat I/O,
the per-user state machine (`NORMAL` / `AWAITING_TOKEN` / `AWAITING_RESET`),
onboarding/token handling, the five slash commands, per-user concurrency locks,
the keep-typing indicator, message truncation, and the background heartbeat and
daily-cleanup tasks — plus `main()` startup/shutdown wiring. It delegates all
LLM/tool work to `llm.process_message` and all persistence to `db`. The bot
operates in **private chats only**; in a group it replies that it only works in
private and does nothing else. Each user is fully isolated.

## Build order position
This is module 10 of 11. The modules listed under "Already implemented" below
are complete and tested. Do not modify them. (`scripts/rotate_key.py`, module
11, is separate — do not implement it here.)

## Already-implemented interfaces
Call dependencies via module attribute access (`import db`, `import llm`, …) so
tests can patch `bot.db.*`, `bot.llm.*`, etc.

- `config.py`: `TELEGRAM_BOT_TOKEN: str`, `CONVERSATION_RETENTION_DAYS: int`,
  `LOG_RETENTION_DAYS: int` (and others). Read all config from `config`.
- `logger.py`: `sanitize(data: dict) -> dict`;
  `log_stdout(level, event, user_id, data) -> None` (never raises).
- `messages.py`: `get(key: str, lang: str, **kwargs) -> str` — unknown `lang`
  → falls back to `"en"`; unknown `key` raises `KeyError`. Keys used:
  `welcome`, `token_accepted`, `token_invalid`, `token_network_error`,
  `token_deletion_failed`, `token_accidental`, `already_registered`,
  `unregistered`, `reset_prompt`, `reset_confirmed` (`count`),
  `reset_confirmed_empty`, `reset_cancelled`, `refresh_confirmed`,
  `group_chat_rejected`, `help_text`, `please_wait`, `decrypt_error`,
  `send_error`, `db_error`.
- `crypto.py`: `encrypt_token(plain: str) -> str`,
  `decrypt_token(encrypted: str) -> str` — raises `ValueError` on failure
  (caller catches `ValueError` → sends `decrypt_error`).
- `language.py`: `detect(text: str) -> str` (never raises);
  `resolve(stored_language: str | None, text: str) -> tuple[str, bool]`
  (returns `(lang, changed)`; raises `TypeError` only if `text is None`).
- `db.py` (async; via `db.`): `init_pool()`, `close_pool()`,
  `get_user(user_id) -> dict | None`,
  `save_user(user_id, encrypted_token, language)`,
  `update_language(user_id, language)`, `touch_user(user_id)`,
  `clear_user_history(user_id) -> int`,
  `cleanup_old_conversations(days) -> int`, `cleanup_old_logs(days) -> int`,
  `log(user_id, level, event, data)`.
- `mcp.py` (async; via `mcp.`): `evict_cache(token: str) -> None`.
- `llm.py` (async; via `llm.`):
  `process_message(user_id, user_text, token, language, turn_start) -> str` —
  returns a plain answer string; `turn_start` must be the timezone-aware
  datetime taken AFTER lock acquisition.

Do not reimplement any of the above.

## Database tables used
None directly — `bot.py` performs NO SQL of its own. All persistence goes
through `db.py` (`users` and `conversations` via the functions above;
`cleanup_old_logs` over `logs`). All SQL stays in `db.py`.

## Module contract
(technical-spec.md → bot.py + Command Handlers)

**States** (in-memory, lost on restart): `NORMAL`, `AWAITING_TOKEN`,
`AWAITING_RESET`.

**Per-user lock (ALL handlers):** `lock = context.user_data.setdefault("lock",
asyncio.Lock())`. If `lock.locked()`: message handler discards silently, command
handlers send `please_wait`, then return. Otherwise `await lock.acquire()` and
release in a `finally` block.

**Group chat guard:** check `update.effective_chat.type == "private"` at the top
of every handler, before the lock. If not private, send `group_chat_rejected`
and stop.

**Startup (`main()`):** `db.init_pool()` (failure → log stdout, exit non-zero)
→ start heartbeat task → start daily-cleanup task → register SIGTERM/SIGINT →
start polling. Record a start timestamp before init for `uptime_seconds`.

**Shutdown:** stop polling, `db.close_pool()` in a `finally` block.

**Message handler:**
1. Private-chat guard.
2. Acquire lock — contention: discard silently.
3. `turn_start = datetime.now(timezone.utc)` (timezone-aware).
4. try/finally for lock release.
5. If `AWAITING_TOKEN` → route to token handler.
6. `db.get_user(user_id)`: DB exception → log ERROR, send `db_error` in
   `language.detect(text)`, return. `None` → send `unregistered` in
   `language.detect(text)`, return.
7. `AWAITING_RESET` (user loaded → stored language available): `"confirm"`
   (stripped, case-insensitive) → `db.clear_user_history`; count > 0 →
   `reset_confirmed`, count == 0 → `reset_confirmed_empty`; state `NORMAL`,
   return. Any other text → `reset_cancelled`, state `NORMAL`, return.
8. `language.resolve(user["language"], user_text)` → `(lang, changed)`.
9. `changed` → `db.update_language`; not changed → `db.touch_user`.
10. Accidental token (40-char alphanumeric) → delete message, `token_accidental`,
    return.
11. `crypto.decrypt_token`. `ValueError` → `decrypt_error`, return.
12. Start keep-typing task.
13. `llm.process_message(user_id, user_text, token, lang, turn_start)`.
14. Cancel keep-typing ALWAYS (in `finally` / explicit cancel).
15. `_send_with_truncation(chat_id, response, lang)`.

**`_send_with_truncation(chat_id, text, lang) -> None`:** split priority — last
`\n\n` before 4096, else last `\n` before 4096, else hard cut at 4096. Per
chunk `bot.send_message` in try/except; on failure send `send_error` via a
direct `bot.send_message` (never recurse into `_send_with_truncation`); if both
fail, log to stdout only.

**Token input handler (lock held by caller):** resolve language from
`context.user_data.get("detected_language")` (else `language.detect`, else
`"en"`) → delete the token message (failure → `token_deletion_failed`,
continue) → validate via `GET /rest/v2/projects` (200 = valid; non-200 →
`token_invalid`, keep `AWAITING_TOKEN`, return; network exception →
`token_network_error`, keep `AWAITING_TOKEN`, return) → if user exists,
`mcp.evict_cache(old_token)` → `crypto.encrypt_token` →
`db.save_user(user_id, encrypted, lang)` → state `NORMAL` → log `new_user` →
clear `detected_language` → send `token_accepted`.

**Keep-typing:** send typing action every 4s; cancelled after step 13 (always);
catch `asyncio.CancelledError` on cancel.

**Heartbeat:** sleep 60s before the first event, then every 60s log `heartbeat`
at INFO with `status: "alive"` and `uptime_seconds`. Log failure does not
terminate the task.

**Daily cleanup:** use `datetime.now(timezone.utc)` (never local). Sleep until
3:00 UTC. DB cleanup calls in try/except: success → `daily_cleanup` INFO;
failure → ERROR, continue. Sleep 60s after a run. Never terminates on error.

**Command handlers** (all: private guard → lock (contention → `please_wait`) →
try/finally; all strings via `messages.py`):
- `/start` — detect language, store in `context.user_data["detected_language"]`;
  load user; exists → `already_registered` (stored lang); none → `welcome`
  (detected lang), state `AWAITING_TOKEN`.
- `/token` — detect language, store; send `welcome`; state `AWAITING_TOKEN`.
- `/reset` — load user; none → `unregistered`, return; else `reset_prompt`
  (stored lang), state `AWAITING_RESET`.
- `/refresh` — load user; none → `unregistered`, return; decrypt token;
  `mcp.evict_cache`; `refresh_confirmed` (stored lang).
- `/help` — load user; exists → stored lang; none → `language.detect(text)`;
  send `help_text`.

## Relevant error handling rules
(technical-spec.md → Error Handling Rules — those touching this module)

- **Rule 1** — external failures → user-facing message in the user's language,
  never silent.
- **Rule 3** — token decryption failure → `decrypt_error`, prompt `/token`.
- **Rule 7** — `send_message` failure → `send_error` via direct
  `bot.send_message`; both fail → stdout only.
- **Rule 8** — token deletion failure → `token_deletion_failed`, continue.
- **Rule 9** — language detection failure → `"en"` silently (handled inside
  `language.detect`).
- **Rule 10** — token validation network error → `token_network_error`, keep
  `AWAITING_TOKEN`.
- **Rule 11** — lock contention → messages: discard; commands: `please_wait`.
- **Rule 12** — `init_pool` failure → log stdout, exit non-zero.
- **Rule 13** — daily cleanup failure → log ERROR, continue.
- **Rule 14** — early return: user message persisted by `llm`, tool results
  cleaned up, error message not saved to history.
- **Rule 15** — `db.get_user` exception → log ERROR, `db_error` in detected
  language.

## Test cases
(technical-spec.md → tests/test_bot.py) — no real network/Telegram calls; mock
`bot.db.*`, `bot.llm.*`, `bot.mcp.*`, the Telegram bot/update objects, and the
HTTP client used for token validation. Mock `asyncio.sleep` and
`datetime.now(timezone.utc)` for the background tasks.

**Group chat guard:** group message → `group_chat_rejected`, no processing;
private → processing continues.

**Per-user lock:** second concurrent message while held → discarded silently;
second concurrent command while held → `please_wait`; lock released after
success; lock released when handler raises (try/finally); keep-typing cancelled
when `llm.process_message` raises (finally runs).

**State machine — transitions:** `NORMAL` → message handler runs;
`AWAITING_TOKEN` → token handler for non-command; `AWAITING_RESET` + `"confirm"`
/ `"CONFIRM"` → cleared, `NORMAL`; `AWAITING_RESET` + other → cancelled,
`NORMAL`; `/start` new → `AWAITING_TOKEN`; `/start` existing →
`already_registered`, unchanged; `/token` → `AWAITING_TOKEN`.

**State machine — sequences:** `/start` → valid token → `token_accepted`,
`NORMAL`; `/start` → invalid token → `token_invalid`, still `AWAITING_TOKEN` →
valid → `token_accepted`, `NORMAL`; `/reset` → `"confirm"` → cleared, `NORMAL`;
`/reset` → other → `reset_cancelled`, `NORMAL`; `/token` → network-error token →
`token_network_error`, `AWAITING_TOKEN` → valid → `token_accepted`.

**Message handler — load order & paths:** user loaded (step 6) before
`AWAITING_RESET` (step 7); `db.get_user` raises → `db_error` in detected
language, returns cleanly; language changed → `update_language` (not
`touch_user`); unchanged → `touch_user` (not `update_language`); unregistered →
`unregistered`; accidental 40-char hex → message deleted, `token_accidental`;
`decrypt_token` raises → `decrypt_error`; `turn_start.tzinfo is not None`.

**Token input handler:** valid → message deleted, `save_user` called,
`token_accepted`; non-200 → `token_invalid`, stays `AWAITING_TOKEN`; network
error → `token_network_error`, stays `AWAITING_TOKEN`; deletion fails →
`token_deletion_failed`, continues; existing user → `mcp.evict_cache` with the
OLD token; language from `context.user_data` (not the token string);
`detected_language` cleared on success; preserved on invalid token.

**`_send_with_truncation`:** under 4096 → single message; `\n\n` before 4096 →
split there; `\n` (no `\n\n`) before 4096 → split there; no newline → hard cut
at 4096; `send_message` failure → `send_error` via direct `bot.send_message`
(no recursion); both fail → stdout only, no exception.

**Command handlers:** `/reset` loads user, stored language; `/reset`
unregistered → `unregistered`, NOT `AWAITING_RESET`; `/refresh` unregistered →
`unregistered`; `/help` registered → stored language; `/help` unregistered →
detected language.

**Heartbeat:** first `asyncio.sleep` arg is 60; logs `heartbeat` at INFO;
continues after log failure (mock `db.log` to raise).

**Daily cleanup:** sleep duration computed from UTC time (mock
`datetime.now(timezone.utc)`); logs `daily_cleanup` at INFO on success; logs
ERROR and continues on DB failure; never terminates on error.

## Expected output
- A file `bot.py` implementing the contract exactly (handlers, lock pattern,
  keep-typing, truncation, heartbeat, daily cleanup, `main()` startup/shutdown)
- A file `tests/test_bot.py` implementing every test case above
- All tests passing; **coverage ≥ 70% for `bot.py`** (the CLAUDE.md threshold
  for this module specifically — below it blocks deploy like a failure)
- After tests pass: append `bot.py`'s public signatures (handlers, `main`,
  `_send_with_truncation`, background tasks) to `interfaces.md`

## Agent instructions
1. FIRST write `tests/test_bot.py` from the test cases above. Mock all external
   services (`bot.db.*`, `bot.llm.*`, `bot.mcp.*`, Telegram objects, the
   token-validation HTTP client) with `unittest.mock`; mock `asyncio.sleep` and
   `datetime.now` for the background tasks. Do not write implementation yet.
2. Run the tests. They must FAIL (red) — nothing is implemented.
3. THEN write `bot.py` to satisfy the contract.
4. Run the tests again. Iterate until all PASS (green) and coverage ≥ 70%.
5. Match the contract EXACTLY — handler order, the three states, the lock
   pattern, and the `process_message(..., turn_start)` call signature.
6. Call already-implemented interfaces as given via module attribute access
   (`config`, `messages.get`, `crypto`, `language`, `db.*`, `mcp.*`,
   `llm.process_message`) — do not reimplement them.
7. Handle every error per the rules above — acquire `turn_start` AFTER the lock
   and pass it unchanged to `llm`; ALWAYS release the lock and ALWAYS cancel the
   keep-typing task in `finally`; background tasks never terminate on error.
8. Do not add dependencies not already in the project (use
   `python-telegram-bot` 21.x and an httpx client already permitted; no blocking
   calls in handlers).
9. Do not modify any other module; do not write SQL; do not implement
   `scripts/rotate_key.py`.
10. When all tests pass, append public signatures to `interfaces.md`.
11. If the contract is ambiguous or conflicts with an interface, STOP and ask —
    do not guess.

Activate `.venv` before any `pytest`/`python`/`pip` command.
