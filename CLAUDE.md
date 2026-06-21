# Project Rules — Todoist Q&A Task Assistant

You are implementing this project one module at a time. Read this file before
every task. These rules override any conflicting instinct.

The authoritative documents are:
- `Technical Spec.md` — module contracts, test contracts, error rules, schema
- `Business Brief.md` — product scope and rationale (takes precedence on conflict)
- `dependency-order.md` — the build order (which module to implement when)
- `interfaces.md` — living record of completed public signatures (read before coding)

## Workflow: test-first, always

For every module:
1. Read the module task, the contract in `Technical Spec.md`, and `interfaces.md`.
2. Write the tests FIRST from the spec's test cases for that module.
3. Run the tests — confirm they FAIL (nothing implemented yet).
4. Write the implementation to satisfy the contract.
5. Run the tests — iterate until ALL PASS and coverage thresholds are met.
6. Append the module's public signatures to `interfaces.md`.
7. Stop. Wait for the next task.

Never write implementation before tests. Never declare done with failing tests.
Implement exactly ONE module per task — never two.

## Must NEVER

- Modify any module other than the one in the current task
- Log secrets, tokens, passwords, or keys — route log data through `logger.sanitize`
- Skip, comment out, weaken, or delete tests to make them pass
- Add a dependency not listed in `Technical Spec.md` → Dependencies
- Guess a function signature — read it from `interfaces.md`
- Reimplement a function that already exists in `interfaces.md`
- Invent behaviour not in the contract
- Use naive datetimes — always `datetime.now(timezone.utc)`
- Read `os.environ` directly — read all configuration from `config`
- Write SQL anywhere except `db.py`
- Hardcode user-facing strings — they live in `messages.py`
- Catch exceptions silently — handle per the spec's numbered error rules
- Make real network calls in unit tests — mock all external services

## Must ALWAYS

- Match contract signatures EXACTLY (names, parameters, return types, async)
- Handle every failure per the spec's 15 numbered Error Handling Rules
- Use timezone-aware UTC datetimes
- Read configuration through `config` only
- Use `db.py` for all database access
- Use `logger` (`log_stdout` / `db.log`) for all logging, via `sanitize`
- Keep all user-facing strings in `messages.py`
- Release `asyncio` locks in a `finally` block
- Cancel the keep-typing task in `finally` (bot handlers)
- Wrap external calls (LLM, MCP, DB, Telegram) per the error rules

## Coding conventions

- Language: Python 3.11+
- Style: PEP 8, type hints on all public functions
- Async: `python-telegram-bot` 21.x, `asyncpg`, `httpx[http2]` — all I/O is async;
  no blocking calls in handlers
- No module-level side effects except `config` (loads env at import) and
  `language` (`DetectorFactory.seed = 0` at import for determinism)

## Test conventions

- Framework: `pytest` + `pytest-asyncio` with `asyncio_mode = "auto"`
  (set in `pyproject.toml` / `pytest.ini` — do not decorate every async test)
- One test file per module: `tests/test_<module>.py`
- `test_db.py` uses a real PostgreSQL test DB via `TEST_DATABASE_URL` (never the
  production DB), with per-test transaction rollback for isolation; migrations
  run once via a session-scoped fixture
- No real network calls in unit tests — mock external services with
  `unittest.mock`
- Coverage threshold: 80% general, 70% for `bot.py`. Below threshold blocks
  deploy the same as a failing test
- Tests must be deterministic — no reliance on wall-clock timing or random seeds
  (mock `time.time` / `datetime.now` where needed)

## File structure

Repo root modules (one test file each under `tests/`):
- `config.py`, `messages.py`, `language.py`, `logger.py`, `crypto.py`
- `db.py`, `mcp.py`, `llm.py`, `bot.py`
- `migrations/` — Alembic (`001_init`, `002_add_indexes`); psycopg2 for migrations,
  asyncpg at runtime
- `scripts/rotate_key.py` — key rotation CLI (run during downtime)
- `tests/` — one test file per module
- `interfaces.md` — living record of completed public signatures
- `dependency-order.md` — build order

## Git conventions

- One commit per completed module (tests + implementation together)
- Commit message: "Implement <module>: <one-line summary>"
- Do not commit secrets, `.env`, or generated artifacts
- Do not commit with failing tests

## Stop and ask — do not guess — when

- The contract is ambiguous or self-contradictory
- The contract conflicts with an existing interface in `interfaces.md`
- The spec and the brief conflict (the brief takes precedence — but flag it)
- A required dependency is not listed in the project dependencies
- A test case cannot be satisfied without violating the contract
- An error rule does not cover a failure mode you encounter
- Implementing the module would require modifying another module

In all these cases: STOP, explain the conflict, and ask. A wrong guess costs
more than a question.
