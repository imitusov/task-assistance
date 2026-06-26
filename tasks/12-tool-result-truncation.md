# Task: Tool-result truncation (history size budget)

Post-v1 enhancement 1 of 3. Implement ONLY this; do it before task 13 (the
bounded loop builds on the same llm.py tool-result handling) and task 14.
Follow `CLAUDE.md` (test-first, read `interfaces.md` first, config via `config`).

## Product context
The bot resends conversation history to the LLM every turn. A single large tool
result (observed: a 77.8KB `find-activity` payload) stays in the
`MAX_HISTORY_MESSAGES` window and bloats context for ~20 turns — one such result
drove an unrelated next question to ~79K tokens and a 24s near-failure. Capping
each tool result before it is stored bounds the worst case.

## Affected modules
`llm.py` (apply the cap), plus a minimal additive `config.py` var. No other
module changes. (`db.py`'s contract already states the caller truncates — no db
code change needed.)

## Already-implemented interfaces
- `llm.py`: `process_message(...)`; tool results currently built as
  `{"role": "tool", "tool_call_id": ..., "content": json.dumps(result)}` then
  `db.save_tool_result(user_id, tool_msg)`.
- `db.py`: `save_tool_result(user_id, tool_content)` — inserts, no trim.
- `config.py`: optional vars read via `os.environ.get(...)`.

## Contract (spec: db.py "History size budget")
- Add `config.MAX_TOOL_RESULT_CHARS: int` (default `8000`).
- In `llm.py`, after `json.dumps(result)` and BEFORE building/saving the tool
  message, truncate the serialized content to `MAX_TOOL_RESULT_CHARS`, appending
  an explicit `…[truncated]` marker when truncation occurs. The oversized blob
  must never reach `db.save_tool_result` / history.
- Truncation is on the serialized string length; keep it valid as message
  content (it's stored as a JSON string, so a truncated string is fine — do not
  attempt to keep it parseable JSON).

## Relevant error rules
None new. Truncation must not change the tool_failure paths (Rule 6) — it only
shrinks the stored content of a successful result.

## Test cases (tests/test_llm.py)
- A tool result whose `json.dumps` exceeds `MAX_TOOL_RESULT_CHARS` → the content
  passed to `save_tool_result` is ≤ the cap (plus marker) and ends with the
  `…[truncated]` marker.
- A small tool result is stored unchanged (no marker).
- Boundary: content exactly at the cap is not marked/truncated.
- The truncation does not affect what is sent to the model in the SAME turn vs.
  what is stored — confirm the stored/forwarded content is the truncated form
  consistently (decide and assert one behavior; storing and forwarding the same
  truncated string is simplest).
- `config` default is 8000 and is overridable via env.

## Expected output
- `config.py` gains `MAX_TOOL_RESULT_CHARS`.
- `llm.py` truncates tool-result content before saving.
- `tests/test_llm.py` covers the cases above; full suite green (≥80%).
- Update `interfaces.md` (config var + llm.py note).

## Agent instructions
1. Write the failing tests first; confirm they fail.
2. Add the config var; implement truncation in `llm.py`.
3. Run tests until green; run the full suite with
   `TEST_DATABASE_URL=postgresql://imitusov@localhost:5432/taskassistant_test`.
4. Update `interfaces.md`. Do not touch other modules.
5. If anything is ambiguous, STOP and ask.

Activate `.venv` first.
