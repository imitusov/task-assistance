# Task: Implement `scripts/rotate_key.py`

## Product context
This is a personal Telegram bot that lets multiple independent users manage and
query their own Todoist tasks; each user's Todoist API token is stored
Fernet-encrypted in PostgreSQL, keyed by a single `SECRET_KEY` that the operator
generates once and backs up. This module is the **manual key-rotation utility**:
an offline CLI, run during bot downtime, that re-encrypts every stored token
from an OLD Fernet key to a NEW one. It exists because tokens are unrecoverable
if the key is ever compromised or needs cycling — rotation lets the operator
move all users to a new key without forcing everyone to re-enter their token.
Automated rotation is explicitly out of scope for v1 (brief: "Manual rotation
script sufficient for v1").

## Build order position
This is module 11 of 11 (the last). Every module under "Already implemented"
below is complete and tested. Do not modify any of them.

## Already-implemented interfaces
Call dependencies via module attribute access (`import db`) so tests can patch
`rotate_key.db.*`.

- `config.py` — importing it loads/validates env at import time (needs the
  standard env vars present, e.g. `DATABASE_URL`). The script does NOT read
  `config.SECRET_KEY` for the transform — the keys come from the CLI (see the
  conflict note below).
- `db.py` (async; via `db.`):
  - `init_pool() -> None` — open the asyncpg pool; call once at startup.
  - `close_pool() -> None` — close it; call in a `finally` block.
  - `get_all_users() -> list[dict]` — all user rows (keys include
    `telegram_user_id`, `todoist_token`); **key-rotation use only**.
  - `update_token(user_id: int, encrypted_token: str) -> None` — updates only
    `todoist_token`. Each call acquires its own pooled connection (see the
    transaction conflict note below); **key-rotation use only**.
- `crypto.py` — `encrypt_token(plain) -> str` / `decrypt_token(encrypted) -> str`
  EXIST but are **hardwired to `config.SECRET_KEY`** (`Fernet(config.SECRET_KEY
  .encode())` inside each call). They CANNOT encrypt/decrypt with the CLI
  `--old-key` / `--new-key`, so they are unusable for rotation (see conflict
  note). `cryptography.fernet.Fernet` is already a project dependency.

Do not reimplement or modify any of the above.

## Database tables used
`users` (only the `todoist_token` column is rewritten), accessed exclusively
through `db.get_all_users` and `db.update_token`. The script writes NO SQL of
its own — all SQL stays in `db.py`.

| Column | Type | Notes |
|---|---|---|
| `telegram_user_id` | BIGINT | PK — identifies the row to update |
| `todoist_token` | TEXT | Fernet-encrypted; decrypt with old key, re-encrypt with new key |

## Module contract
(technical-spec.md → scripts/rotate_key.py)

- **CLI:** `--old-key` and `--new-key`, both **required**. Run during bot
  downtime.
- Process users in **batches of 50** (a batch is a progress / error-isolation
  grouping — see the resolved decision below).
- **Per user:** decrypt `todoist_token` with the OLD key, re-encrypt with the
  NEW key, then `db.update_token(user_id, new_encrypted)` (each call commits on
  its own).
- **On per-user failure:** log the failed `user_id` with the traceback, skip
  that user, and continue — one bad row (or batch) must not abort the whole run.
- Print **per-batch progress** and a **final summary** (counts of succeeded /
  failed).
- **Does NOT delete the old key** — rotation only re-encrypts; key disposal is a
  separate manual step.

**Resolved decision (maintainer-confirmed 2026-06-24):** the spec's literal
"single DB transaction per batch with rollback" is NOT implemented, because the
existing `db.update_token` acquires its own connection per call and exposes no
shared-transaction handle (changing `db.py` is out of scope). For this v1 manual
utility, "batch" is a grouping for progress reporting and error isolation only;
each `update_token` is its own commit, and a failing row is logged-and-skipped
rather than rolled back. This is an intentional deviation from the literal
contract — do NOT extend `db.py` or stop to ask about it.

## Relevant error handling rules
The spec's 15 numbered Error Handling Rules govern the live bot's request flow;
**none apply directly** to this offline operator script. The script's own error
handling is the contract above: catch per-user exceptions, log `user_id` +
traceback, skip that user, continue; never let one failure abort the run; emit a
final succeeded/failed summary. Never log a plaintext token or either key
(consistent with the project's no-secrets-in-logs rule).

## Test cases
(The spec defines no `test_rotate_key.py`; these are derived from the contract to
meet the coverage threshold. Mock `rotate_key.db.*`; use REAL `Fernet` keys so
the decrypt-old / encrypt-new round-trip is exercised for real.)

- **Round-trip:** given a token encrypted under the old key, after a run the
  stored value passed to `update_token` decrypts to the original plaintext under
  the NEW key (and no longer under the old key).
- **Batching:** with 120 users, processing happens in batches of 50
  (50 / 50 / 20) — assert the batch boundaries (e.g. number of transactions /
  progress lines).
- **`update_token` called once per user** with the re-encrypted value and the
  correct `user_id`.
- **Both CLI args required:** missing `--old-key` or `--new-key` exits non-zero
  with a usage error (argparse).
- **Failure isolation:** make one row raise (e.g. one user's `decrypt` or
  `update_token` raises) → its `user_id` + traceback are logged, that user is
  skipped, and all remaining users (including the rest of the same batch) still
  process; the final summary reports the failed count. (Per the resolved
  decision, isolation is per-row, not an atomic per-batch rollback.)
- **Final summary:** succeeded / failed counts printed and correct for a mixed
  run.
- **Empty DB:** `get_all_users` returns `[]` → no batches, clean summary, exit 0.
- **No plaintext token or key appears in any logged/printed output.**
- **Pool lifecycle:** `init_pool` called at start, `close_pool` called in
  `finally` even when a batch raises.

## Expected output
- A file `scripts/rotate_key.py` implementing the contract exactly
- A file `tests/test_rotate_key.py` implementing every test case above
- All tests passing (≥80% coverage)
- After tests pass: append `scripts/rotate_key.py`'s public surface (CLI args,
  `main()` / any rotation helper signatures, batching behavior) to
  `interfaces.md`

## Agent instructions
1. FIRST write `tests/test_rotate_key.py` from the test cases above. Mock
   `rotate_key.db.*` with `unittest.mock` (no real Postgres); use real `Fernet`
   keys for the crypto round-trip. Do not write implementation yet.
2. Run the tests. They must FAIL (red) — nothing is implemented.
3. THEN write `scripts/rotate_key.py` to satisfy the contract.
4. Run the tests again. Iterate until all PASS (green) and coverage is met.
5. Match the contract EXACTLY — required `--old-key` / `--new-key`, batches of
   50, per-batch transaction semantics, continue-on-failure, final summary.
6. Call already-implemented interfaces as given (`db.init_pool`,
   `db.close_pool`, `db.get_all_users`, `db.update_token`) via module attribute
   access — do not reimplement them.
7. Use `cryptography.fernet.Fernet` directly with the two CLI keys for the
   transform (NOT `crypto.encrypt_token` / `crypto.decrypt_token`, which are
   bound to `config.SECRET_KEY`). Never log a plaintext token or either key.
8. Do not add dependencies not already in the project; write no SQL; always
   `db.close_pool()` in a `finally` block.
9. Do not modify any other module.
10. When all tests pass, append public signatures to `interfaces.md`.
11. If any NEW ambiguity or interface conflict surfaces, STOP and ask — do not
    guess. The two known conflicts below are already RESOLVED; implement them as
    decided, do not re-open them.

### Two contract/interface conflicts — already RESOLVED (implement as decided)
- **(A) Keys vs. `crypto.py` — RESOLVED.** The contract rotates between
  CLI-supplied `--old-key` / `--new-key`, but `crypto.encrypt_token` /
  `decrypt_token` are hardwired to `config.SECRET_KEY` and cannot take a key
  argument. Decision: the script uses `Fernet(old_key)` / `Fernet(new_key)`
  directly (the `cryptography` lib is already a dependency). This is the one
  legitimate place that bypasses `crypto.py`. Do not call `crypto.*` for the
  transform; do not modify `crypto.py`.
- **(B) "Single transaction per batch" vs. `db.update_token` — RESOLVED.**
  `db.update_token` acquires its own connection per call and exposes no
  shared-transaction handle, so atomic per-batch rollback is impossible without
  changing `db.py` (out of scope). Decision (maintainer-confirmed 2026-06-24):
  do NOT implement per-batch transactions and do NOT extend `db.py`. "Batch" is
  a progress / error-isolation grouping only; each `update_token` commits on its
  own, and a failing row is logged-and-skipped (not rolled back). Implement this
  relaxed behavior directly.

Activate `.venv` before any `pytest`/`python`/`pip` command.
