# Task: Bounded agentic tool loop + wrapper persistence

Post-v1 enhancement 2 of 3. Do AFTER task 12 (preserve its tool-result
truncation) and BEFORE task 14 (confirmation intercepts inside this loop).
Follow `CLAUDE.md` (test-first, read `interfaces.md` first).

## Product context
v1's `process_message` runs exactly one tool round then forces an answer. When a
tool returns an error result (e.g. Todoist rejects an invalid filter,
`isError: true`), the model wants to retry but the rigid flow gives it nowhere
to go — so its retry narration ("Let me try a different search…") becomes the
final answer and the user gets a non-answer. This task replaces the single round
with a bounded loop, and fixes a coupled latent bug: intermediate assistant
tool-call wrapper messages are never persisted, leaving orphan `role:"tool"`
rows in reloaded history (malformed per the OpenAI/Qwen tool format —
confirmed in the dev DB: a stored turn went `user → tool → assistant-answer`
with no `assistant`/`tool_calls` row between).

## Affected modules
`llm.py` (the loop), `db.py` (new no-trim insert for the wrapper), plus additive
`config.MAX_TOOL_ROUNDS`, and a system-prompt line in `llm.py`'s
`_SYSTEM_PROMPTS`.

## Already-implemented interfaces
- `llm.py`: `process_message(user_id, user_text, token, language, turn_start)`;
  `_call_llm(messages_payload, tools, user_id)`;
  `_run_llm_call(stage, messages_payload, tools, user_id, language, turn_start,
  cleanup_on_failure)`; `_SYSTEM_PROMPTS`.
- `db.py`: `save_user_message`, `save_tool_result`, `save_turn` (the only
  trimmer), `delete_turn_tool_results(user_id, since)`.
- `mcp.py`: `call_tool(token, name, arguments) -> dict` — returns the `result`
  dict (e.g. `{"content": [...], "isError": false}`); RAISES only on
  transport/JSON-RPC error, NOT on `isError: true`.

## Contract (spec: llm.py "Bounded agentic tool loop")
- Add `config.MAX_TOOL_ROUNDS: int` (default `3`). Per turn: at most
  `MAX_TOOL_ROUNDS` tool-requesting calls + 1 forced final call (≤4 LLM calls).
- Loop: each LLM call uses `tool_choice:"auto"`, 30s timeout; log `llm_call`
  with `stage:"tool_round_<n>"`. 429/timeout handling unchanged (cleanup via
  `delete_turn_tool_results(user_id, turn_start)` if any tool results were
  persisted this turn).
- No `tool_calls` in a response → final answer: `save_turn`, log `bot_response`,
  return `content`.
- Has `tool_calls`:
  - **Persist the assistant tool-call wrapper** (`role:"assistant"`, `content`,
    `tool_calls`) via the new no-trim `db.save_assistant_tool_call` AND append
    to in-memory history (so reloaded history is well-formed).
  - For each call: `mcp.call_tool` (EXCEPTION → log ERROR,
    `delete_turn_tool_results(turn_start)`, return `tool_failure`); serialize +
    truncate to `MAX_TOOL_RESULT_CHARS` (task 12); `save_tool_result`
    (EXCEPTION → same cleanup + `tool_failure`); append; log `tool_call`.
  - An `isError: true` result is NOT an exception — append and continue so the
    model can retry.
- **Exhaustion**: after `MAX_TOOL_ROUNDS` if the last response still requested
  tools, make ONE final call with `tool_choice:"none"` (stage
  `"answer_generation"`), `save_turn`, return. Never return bare retry narration
  (Error Rule 16).
- All cleanup passes the exact `turn_start`.
- **System prompt**: add a line instructing the model to read tool errors and
  retry with a corrected call rather than giving up (needed under `-noreason`).

### db.py addition
`save_assistant_tool_call(user_id: int, content: dict) -> None` — inserts a row
with `role = content["role"]` (`"assistant"`), no trim. (Implementation may
generalize an existing no-trim insert instead of a new name; requirement is:
persisted, no-trim, in order.)

## Relevant error rules
Rule 6 (exception vs `isError`), Rule 14 (early-return cleanup), Rule 16
(loop exhaustion → forced answer).

## Test cases (tests/test_llm.py, tests/test_db.py)
- Single round (no tools) still works: `save_turn` once, answer returned.
- One tool round → wrapper persisted (assert `save_assistant_tool_call` called
  with role assistant + tool_calls), tool result saved, follow-up call, answer.
- Multi-round: round-1 tools → round-2 tools → round-3 answer; assert ≤
  `MAX_TOOL_ROUNDS+1` LLM calls and tools executed each round.
- `isError: true` result → NOT treated as `tool_failure`; loop continues; the
  result is fed into the next call's messages.
- Exhaustion: model requests tools every round to the cap → a final
  `tool_choice:"none"` call is made and its text returned; no bare narration.
- Exception from `mcp.call_tool` / `save_tool_result` → `delete_turn_tool_results`
  with the exact `turn_start`, `tool_failure` returned.
- `stage` logged as `tool_round_<n>` then `answer_generation`.
- (db) `save_assistant_tool_call` inserts role=assistant, does NOT trim; after a
  full turn, reloaded `get_history` is well-formed (each `tool` row preceded by
  its `assistant`/`tool_calls`).

## Expected output
- `config.MAX_TOOL_ROUNDS`; rewritten loop in `llm.py`; `save_assistant_tool_call`
  in `db.py`; system-prompt line; tests; full suite green (≥80%).
- Update `interfaces.md` for all three modules.

## Agent instructions
1. Write the failing tests first (mock `llm.db.*`, `llm.mcp.*`, the httpx client;
   real Postgres for the db test). Confirm they fail.
2. Implement: db function → config var → loop → system prompt.
3. Preserve task-12 truncation in the per-result handling.
4. Run until green; full suite with the test DB URL.
5. Update `interfaces.md`. Don't expand scope to confirmation (task 14).
6. If ambiguous, STOP and ask. Activate `.venv` first.
