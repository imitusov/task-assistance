# Task: Implement `config.py` (module 1 of 11)

Follow the workflow and rules in `CLAUDE.md`. Implement only this module.
Read `interfaces.md` first — it is empty, so config has no upstream dependencies.

## Module contract (Technical Spec.md → config.py)

Loads all env vars at import time. Required vars raise `KeyError` immediately
if missing. All modules import from `config` — never `os.environ` directly.

Required: TELEGRAM_BOT_TOKEN, NEURALDEEP_API_KEY, NEURALDEEP_API_URL,
DATABASE_URL, SECRET_KEY, LOG_LEVEL

Optional with int defaults:
- MCP_TOOLS_TTL = 86400
- MAX_HISTORY_MESSAGES = 20
- CONVERSATION_RETENTION_DAYS = 7
- LOG_RETENTION_DAYS = 30

## Test cases

The spec defines no test contract for config. Write minimal tests encoding the
contract (use monkeypatch/env fixtures; reload the module so import-time loading
is exercised):
- All required vars set → import succeeds, every value is exposed
- A missing required var → raises `KeyError`
- Optional vars absent → documented int defaults (86400, 20, 7, 30)
- Optional vars set as string numbers → parsed as `int`

## Project setup (module 1 establishes this once)

- `pyproject.toml` (or `pytest.ini`) with:
  ```
  [tool.pytest.ini_options]
  addopts = "--cov=. --cov-fail-under=80"
  asyncio_mode = "auto"
  ```
- `requirements.txt` pinned per Technical Spec.md → Dependencies:
  ```
  python-telegram-bot==21.*
  asyncpg
  psycopg2-binary
  httpx[http2]
  cryptography>=42.0.0
  alembic>=1.13.0
  sqlalchemy>=2.0.0
  langdetect
  pytest
  pytest-asyncio
  pytest-cov
  ```

## Expected output

- `config.py` implementing the contract exactly
- `tests/test_config.py` covering every case above, all passing
- `pyproject.toml`/`pytest.ini` and `requirements.txt` created
- After tests pass: append config's public surface (constant names + types,
  required vs default) to `interfaces.md` following its format

## Notes

- Required vars raising `KeyError` must NOT be silently defaulted.
- Do not implement or stub any other module.
- If anything in the contract is ambiguous, STOP and ask (see CLAUDE.md).
