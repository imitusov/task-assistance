# Task: Destructive-action confirmation (AWAITING_TOOL_CONFIRM)

Post-v1 enhancement 3 of 3. Do AFTER task 13 — confirmation intercepts a
destructive tool call inside the bounded tool loop. Follow `CLAUDE.md`
(test-first, read `interfaces.md` first).

## Product context
The bot executes destructive Todoist tool calls (e.g. `delete-object`) on
inferred intent alone — no confirmation. Irreversible actions should require an
explicit user confirmation, the same two-step pattern as `/reset`. Decided
mechanism (2026-06-26): in-conversation two-step confirm, enforced in code (not
left to the model, which under `-noreason` won't reliably self-restrain).

## Affected modules
`llm.py` (detect destructive tool, surface a confirmation request instead of
executing), `bot.py` (new state, store pending call, handle confirm/cancel),
`messages.py` (two new strings), additive `config.DESTRUCTIVE_TOOLS`.

## Already-implemented interfaces
- `llm.py`: `process_message(...) -> str` (the bounded loop from task 13).
- `bot.py`: states `NORMAL`/`AWAITING_TOKEN`/`AWAITING_RESET`; per-user lock
  pattern; message handler calls `llm.process_message(...)`; `AWAITING_RESET`
  confirm/cancel handling is the pattern to mirror.
- `messages.py`: `get(key, lang, **kwargs)`.
- `mcp.py`: `call_tool(token, name, arguments)`.

## Contract (spec: bot.py "Destructive-action confirmation")
- Add `config.DESTRUCTIVE_TOOLS` (set of tool names; default `{"delete-object"}`).
- **llm.py**: in the tool loop, BEFORE executing a tool whose name is in
  `DESTRUCTIVE_TOOLS`, stop and return a structured **confirmation-required**
  result instead of a plain answer string — carrying the pending tool call(s)
  and a human-readable description. This relaxes the "returns plain strings
  only" rule: `process_message` now returns either an answer `str` OR a
  confirmation-request object (still Telegram-agnostic; `bot.py` renders it).
  Decide a clear return shape (e.g. a small dataclass / typed dict) and document
  it in `interfaces.md`.
- **bot.py**: on a confirmation-required result, store the pending call(s) +
  context needed to resume in `context.user_data`, set state
  `AWAITING_TOOL_CONFIRM`, send `tool_confirm_prompt` (with the description) in
  the user's language.
- **bot.py** next message while `AWAITING_TOOL_CONFIRM`:
  - `"confirm"` (stripped, case-insensitive) → execute the pending tool(s),
    resume answer generation, `save_turn`, return answer; state `NORMAL`.
  - anything else → discard pending call(s), send `tool_confirm_cancelled`;
    state `NORMAL`.
- **messages.py**: add `tool_confirm_prompt` (takes a description kwarg) and
  `tool_confirm_cancelled`, both EN + RU.

### Open sub-decisions (pick and document)
- Where/how to hold the in-flight turn across the two messages — default
  in-memory `user_data` (lost on restart, consistent with existing states).
- Multiple destructive calls in one turn: confirm together (recommended) vs.
  one-by-one.

## Relevant error rules
Rule 11 (lock contention while awaiting confirm → message discard / command
`please_wait`), Rule 14 (early-return cleanup). Resume path must still honor the
loop's `tool_failure`/cleanup semantics (Rule 6) when it executes the pending
tool.

## Test cases (tests/test_llm.py, tests/test_bot.py)
- llm: a `delete-object` tool call → `process_message` returns a
  confirmation-required result (NOT executed, `mcp.call_tool` not called for it).
- llm: a non-destructive tool call → executes normally (no confirmation).
- bot: confirmation-required result → state becomes `AWAITING_TOOL_CONFIRM`,
  `tool_confirm_prompt` sent, pending call stored.
- bot: `AWAITING_TOOL_CONFIRM` + `"confirm"`/`"CONFIRM"` → pending tool executed,
  state `NORMAL`.
- bot: `AWAITING_TOOL_CONFIRM` + other text → not executed,
  `tool_confirm_cancelled`, state `NORMAL`.
- messages: both new keys present in EN and RU.
- config: `DESTRUCTIVE_TOOLS` default + override.

## Expected output
- `config.DESTRUCTIVE_TOOLS`; confirmation surfacing in `llm.py`; new state +
  handling in `bot.py`; two strings in `messages.py`; tests; full suite green
  (≥80% overall, ≥70% bot.py).
- Update `interfaces.md` (the new `process_message` return shape, the bot state,
  config var, message keys).

## Agent instructions
1. Write failing tests first (mock externals; Telegram objects per test_bot.py
   patterns). Confirm they fail.
2. Implement: config set → messages strings → llm.py confirmation surfacing →
   bot.py state + confirm/cancel + resume.
3. Run until green; full suite with the test DB URL.
4. Update `interfaces.md`. This task spans 3 modules by necessity (the feature
   is inherently cross-module) — keep each change minimal and contract-driven.
5. If a sub-decision above is unclear, STOP and ask. Activate `.venv` first.
