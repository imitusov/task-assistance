# Task: Implement `logger.py` (module 4 of 11)

Follow the workflow and rules in `CLAUDE.md`. Implement only this module.
Read `interfaces.md` first to confirm config's actual signatures — `logger`
depends on `config` (reads `LOG_LEVEL`).

## Module contract (Technical Spec.md → logger.py)

**sanitize(data: dict) -> dict**
Scans top-level keys only. Does NOT recurse into nested dicts or lists — this is
an explicit design rule, not a limitation. Case-insensitive key scan. Keys
containing `token`, `api_key`, `secret`, or `password` → `"***REDACTED***"`.
Returns a new dict, does not mutate input. Values that are themselves dicts or
lists are passed through unchanged regardless of contents.

**log_stdout(level, event, user_id, data)**
Single JSON line to stdout. Fields: `timestamp` (ISO 8601 UTC), `level`,
`event`, `user_id`, `data`. Calls `sanitize` on `data`. `user_id` may be None.
Respects `LOG_LEVEL` from config.

## Test cases (Technical Spec.md → tests/test_logger.py)

- `sanitize({"token": "abc123"})` returns `{"token": "***REDACTED***"}`
- `sanitize({"TOKEN": "abc123"})` returns redacted (case-insensitive)
- `sanitize({"todoist_token": "abc"})` returns redacted (key contains "token")
- `sanitize({"message": "hello"})` returns `{"message": "hello"}` (unchanged)
- `sanitize` does not mutate input dict
- `sanitize({"api_key": "x", "normal": "y"})` redacts only `api_key`
- `sanitize({"params": {"token": "secret"}})` does NOT redact the nested `token`
  key — top-level scan only, by explicit design rule
- `log_stdout` writes a single JSON line to stdout (capture with capsys)
- `log_stdout` output contains `timestamp`, `level`, `event`, `user_id`
- `log_stdout` calls `sanitize` — token in data is redacted in output

## Expected output

- `logger.py` implementing the contract exactly
- `tests/test_logger.py` covering every case above, all passing
- After tests pass: append `sanitize` and `log_stdout` signatures to
  `interfaces.md`

## Notes

- `sanitize` is top-level only by design — the nested-not-redacted test
  documents the contract, not a shortcoming. Do not "fix" it to recurse.
- `sanitize` returns a NEW dict; never mutate the input.
- `timestamp` is ISO 8601 in UTC (timezone-aware).
- Do not implement or stub any other module.
- If anything is ambiguous, STOP and ask (see CLAUDE.md).
