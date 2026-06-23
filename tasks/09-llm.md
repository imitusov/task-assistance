# Task: Implement `llm.py`

## Product context
This is a personal Telegram bot that lets multiple independent users manage and
query their own Todoist tasks through natural conversation. This module is the
**orchestration core**: it talks to the Qwen LLM at `https://api.neuraldeep.ru/v1`
(OpenAI-compatible) with tool calling enabled, runs the full conversation turn —
load history, inject a fresh dated system prompt in the user's language, fetch
Todoist tools, let the model call them, execute them, and feed results back for a
final answer. It returns plain answer strings only and has **no Telegram
knowledge** (the bot layer owns chat I/O). On every turn the LLM receives the
conversation history plus tool definitions, and either answers directly or calls
one or more tools (all executed before a second LLM call formulates the answer).

## Build order position
This is module 9 of 11. The modules listed under "Already implemented" below are
complete and tested. Do not modify them. (`bot.py`, module 10, will call
`process_message` — do not implement it here.)

## Already-implemented interfaces
- `config.py`:
  - `NEURALDEEP_API_KEY: str`, `NEURALDEEP_API_URL: str` (LLM endpoint + key)
  - Read all config from `config` — never from `os.environ`.
- `logger.py`: `sanitize`/`log_stdout` exist but `llm.py` does NOT import
  `logger` directly — route all logging through `db.log` (see below).
- `messages.py`:
  - `get(key: str, lang: str, **kwargs) -> str` — unknown `lang` falls back to
    `"en"`; unknown `key` raises `KeyError`. Keys you need: `llm_timeout`,
    `tool_failure`, `rate_limit_session` (`retry_time`), `rate_limit_week`
    (`retry_time`).
- `db.py` (async; call via `db.` attribute access):
  - `get_history(user_id: int) -> list[dict]` — content objects, `created_at`
    ASC, excludes `role="system"`, `[]` if none.
  - `save_user_message(user_id: int, content: dict) -> None` — never trims.
  - `save_turn(user_id: int, assistant_content: dict) -> None` — inserts
    assistant row and trims to `MAX_HISTORY_MESSAGES`; the only place trimming
    happens; call exactly once per successful turn.
  - `save_tool_result(user_id: int, tool_content: dict) -> None` — never trims.
  - `delete_turn_tool_results(user_id: int, since: datetime) -> None` — deletes
    `role="tool"` rows with `created_at >= since`; `since` must be
    timezone-aware (raises `ValueError` if naive).
  - `log(user_id: int | None, level: str, event: str, data: dict) -> None` —
    sanitizes and persists the row to the Postgres `logs` table AND calls
    `logger.log_stdout` internally; never raises. ALL of this module's
    structured logging (`llm_call`, `tool_call`, `rate_limit`, `bot_response`,
    tool-failure `ERROR`s) goes through `db.log` — not through `logger`
    directly — so the events reach the Grafana dashboards (spec Log Events
    table). Do NOT import `logger` in `llm.py`.
- `mcp.py` (async; call via `mcp.` attribute access):
  - `get_tools(token: str) -> list` — OpenAI-format tools
    (`[{"type": "function", "function": {...}}, ...]`), cached per token.
  - `call_tool(token: str, name: str, arguments: dict) -> dict` — returns the
    `result` dict; raises on HTTP error, SSE parse failure, JSON-RPC error, or
    timeout (never swallows).

Call db/mcp via module attribute access (`import db` / `import mcp`) so tests can
patch `llm.db.*` / `llm.mcp.*`. Do not reimplement any of the above.

## Database tables used
None directly — `llm.py` performs NO SQL of its own. All persistence goes
through `db.py` functions (`conversations` table, via `get_history` /
`save_user_message` / `save_turn` / `save_tool_result` /
`delete_turn_tool_results`). All SQL stays in `db.py`.

## Module contract
(technical-spec.md → llm.py)

Returns plain strings only. No Telegram knowledge.

- **System prompts:** EN and RU templates containing `{current_date}`. Inject
  today's date (`datetime.now(timezone.utc)`) in the user's `language`. Never
  stored in the DB.
- **Thinking tokens:** Strip `<think>...</think>` from responses. Implement
  this **unconditionally** (user-confirmed 2026-06-23) — `test_qwen_tools.py`
  was never run, so do not gate the feature on it; the stripper is a safe no-op
  on plain text. Handle nested and incomplete/unclosed blocks, and pass `None`
  through unchanged (response `content` is `null` on a tool-calling turn).
- **Rate limit:** On HTTP 429, read `X-Window` and `Retry-After`, log
  `rate_limit` at **WARNING** (never ERROR), return the appropriate message from
  `messages.py`. No retry.
- **`token_count`:** From `response.usage.total_tokens`; log `null` if absent.

**`process_message(user_id, user_text, token, language, turn_start) -> str`**
`turn_start` is a timezone-aware datetime passed in from `bot.py` after lock
acquisition. Steps:

1. `db.get_history(user_id)`.
2. Prepend the system prompt in `language` with today's date.
3. Build the user message dict.
4. `db.save_user_message(user_id, user_msg)` — ALWAYS, before any LLM call.
5. Append to in-memory history.
6. `mcp.get_tools(token)`.
7. First LLM call: `tool_choice="auto"`, 30s timeout. Log `llm_call`
   (stage `"tool_selection"`, token_count).
8. HTTP 429 → rate-limit message, return (no `save_turn`).
9. Timeout → log WARNING, return `llm_timeout`.
10. Strip thinking tokens if applicable.
11. No tool calls → `db.save_turn`, log `bot_response`, return answer.
12. Tool calls:
    a. Append assistant tool-call message to in-memory history.
    b. For each tool call:
       - `mcp.call_tool`. On failure: log ERROR,
         `db.delete_turn_tool_results(user_id, turn_start)`, return
         `tool_failure` (skip remaining tool calls).
       - Serialize result with `json.dumps(result)`; build the tool message
         dict.
       - `db.save_tool_result`. On failure: log ERROR,
         `db.delete_turn_tool_results(user_id, turn_start)`, return
         `tool_failure`.
       - Append to in-memory history; log `tool_call` (name, params,
         latency_ms).
    c. Second LLM call: `tool_choice="auto"`, 30s timeout. Log `llm_call`
       (stage `"answer_generation"`, token_count).
    d. HTTP 429 → `db.delete_turn_tool_results(user_id, turn_start)`, return
       rate-limit message.
    e. Timeout → `db.delete_turn_tool_results(user_id, turn_start)`, return
       `llm_timeout`.
    f. Strip thinking tokens.
    g. `db.save_turn(user_id, assistant_dict)`.
    h. Log `bot_response` with total latency.
    i. Return the answer text.

**Critical:** every cleanup call must pass the EXACT `turn_start` value received
by `process_message` — never a freshly generated timestamp.

## Relevant error handling rules
(technical-spec.md → Error Handling Rules — only those touching this module)

- **Rule 1** — External (LLM) failures surface as a user-facing message in the
  user's language, never silently.
- **Rule 4** — HTTP 429 → log WARNING, return the rate-limit message, **no
  retry**.
- **Rule 5** — LLM timeout → return `llm_timeout`.
- **Rule 6** — MCP failure or `save_tool_result` failure →
  `delete_turn_tool_results`, return `tool_failure`, discard partial results; do
  NOT proceed to the second LLM call with unpersisted tool results.
- **Rule 14** — Early return: the user message is already saved (step 4), tool
  results are cleaned up, and the error message itself is NOT saved to history.

## Test cases
(technical-spec.md → tests/test_llm.py) — no real network calls; mock the LLM
client, `db.*`, and `mcp.*`.

**Direct answer (no tools):**
- `save_user_message` called before the LLM call (step 4).
- `save_turn` called exactly once; `save_tool_result` never called.
- Returned answer matches the LLM response content.

**Tool calling path:**
- One `tool_call` → `mcp.call_tool` called once, second LLM call made,
  `save_turn` called.
- Two `tool_calls` → `mcp.call_tool` called twice, both results appended before
  the second LLM call.
- `save_tool_result` called once per tool call; `save_turn` exactly once after
  the second LLM response.

**`turn_start` propagation:** on tool failure, rate limit after tool calls, LLM
timeout after tool calls, and `save_tool_result` failure — assert
`delete_turn_tool_results` is called with the EXACT `turn_start` passed into
`process_message` (capture the arg and compare identity), not a new timestamp.

**Early return paths:**
- HTTP 429 first call → rate-limit message; `save_turn` NOT called;
  `save_user_message` WAS called; `delete_turn_tool_results` NOT called.
- HTTP 429 second call → `delete_turn_tool_results` called, rate-limit message.
- LLM timeout first call → `llm_timeout` returned.
- LLM timeout second call → `delete_turn_tool_results` called.
- Tool failure → `delete_turn_tool_results` called, remaining tool calls
  skipped, `tool_failure` returned.
- `save_tool_result` failure → `delete_turn_tool_results` called, `tool_failure`
  returned.

**Rate limit:**
- `X-Window: session` → `rate_limit_session` message.
- `X-Window: week` → `rate_limit_week` message.
- `rate_limit` logged at WARNING, not ERROR.
- `Retry-After` value appears in the returned message.

**Thinking tokens:** (always implemented — not gated on `test_qwen_tools.py`)
- `<think>content</think>answer` → `answer` returned.
- No thinking block → response unchanged.
- Nested and incomplete/unclosed `<think>` blocks handled.
- `None` content passes through unchanged.

**`token_count`:**
- `response.usage.total_tokens` present → logged as integer.
- `response.usage` absent → logged as `null`.

**System prompt:**
- EN system prompt when `language="en"`; RU when `language="ru"`.
- `{current_date}` replaced with today's date.

## Expected output
- A file `llm.py` implementing the contract exactly
- A file `tests/test_llm.py` implementing every test case above
- All tests passing (≥80% coverage)
- After tests pass: append `llm.py`'s public signature(s) (`process_message`,
  and any public helpers such as the thinking-token stripper if exposed) to
  `interfaces.md`

## Agent instructions
1. FIRST write `tests/test_llm.py` from the test cases above. Mock the LLM HTTP
   client, `llm.db.*`, and `llm.mcp.*` with `unittest.mock` (no real network
   calls); mock `datetime.now` where the system-prompt date is asserted. Do not
   write implementation yet.
2. Run the tests. They must FAIL (red) — nothing is implemented.
3. THEN write `llm.py` to satisfy the contract.
4. Run the tests again. Iterate until all PASS (green) and coverage is met.
5. Match the contract signature EXACTLY:
   `process_message(user_id, user_text, token, language, turn_start) -> str`
   (async); follow the step order precisely.
6. Call already-implemented interfaces as given (`config`, `messages.get`,
   `db.*` including `db.log`, `mcp.*`) via module attribute access — do not
   reimplement them, and do not import `logger` directly.
7. Handle every error per the rules above — 429 logs WARNING and never retries;
   timeouts return `llm_timeout`; tool/`save_tool_result` failures clean up and
   return `tool_failure`; always pass the EXACT `turn_start` to cleanup.
8. Do not add dependencies not already in the project (LLM calls go to
   `NEURALDEEP_API_URL` with `tool_choice="auto"` and a 30s timeout; use an
   OpenAI-compatible / httpx client already permitted by the project).
9. Do not modify any other module; do not import `bot.py`; write no SQL.
10. When all tests pass, append public signatures to `interfaces.md`.
11. If the contract is ambiguous or conflicts with an interface, STOP and ask —
    do not guess.

Activate `.venv` before any `pytest`/`python`/`pip` command.
