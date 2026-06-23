# Task: Implement `migrations/` (Alembic — `001_init`, `002_add_indexes`)

## Product context
This is a personal Telegram bot that lets multiple independent users manage and
query their own Todoist tasks through natural conversation, powered by an open
LLM with tool calling, hosted on Railway with observability via Grafana. This
module defines the **database schema** every other data path depends on: the
per-user records, conversation history, and the logs table that feeds Grafana.
Each user's data is fully isolated, so the schema must enforce that at the
foreign-key level.

## Build order position
This is module 6 of 11. The modules listed under "Already implemented" below are
complete and tested. Do not modify them. Do NOT implement `db.py` — that is
module 7, a separate task.

## Already-implemented interfaces
- `config.py`: `DATABASE_URL: str` (required, `KeyError` if unset). Read it from
  `config` — never from `os.environ`.

(No other module is a dependency. `db.py` does not exist yet and must not be
created or stubbed here.)

## Database tables used
This module CREATES all three tables (copied from the spec schema):

**users**
| Column | Type | Notes |
|---|---|---|
| `telegram_user_id` | BIGINT | Primary key |
| `todoist_token` | TEXT | Fernet-encrypted |
| `language` | TEXT | `"en"` or `"ru"`, NOT NULL, DEFAULT `'en'` |
| `created_at` | TIMESTAMPTZ | DEFAULT NOW() |
| `last_active_at` | TIMESTAMPTZ | Updated every turn |

**conversations**
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL | Primary key |
| `user_id` | BIGINT | FK → users **ON DELETE CASCADE** |
| `role` | TEXT | user / assistant / tool — matches `content.role` |
| `content` | JSONB | Full OpenAI-format message object |
| `created_at` | TIMESTAMPTZ | DEFAULT NOW() |

**logs**
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL | Primary key |
| `user_id` | BIGINT | Nullable |
| `level` | TEXT | INFO / DEBUG / WARNING / ERROR |
| `event` | TEXT | |
| `data` | JSONB | Sanitized |
| `created_at` | TIMESTAMPTZ | DEFAULT NOW() |

**Indexes** (created by `002`):
`logs(event)`, `logs(created_at)`, `logs(user_id)`, `logs(level)`,
`conversations(user_id)`.

## Module contract
(technical-spec.md → Migrations)

- Alembic uses **psycopg2 (sync)** for migrations; asyncpg is runtime only and
  must NOT be imported here. The Alembic env rewrites `DATABASE_URL` to
  `postgresql+psycopg2://` for its connection, reading the value from `config`.
- **`001_init.py`** — creates all three tables exactly as above.
  `users.language` is TEXT NOT NULL DEFAULT `'en'`. `conversations.user_id` FK
  is ON DELETE CASCADE. All timestamp columns are TIMESTAMPTZ (never naive
  TIMESTAMP).
- **`002_add_indexes.py`** — creates all five indexes listed above.
  Its `down_revision` must point at `001`.
- Both migrations must implement working `upgrade()` and `downgrade()`.

## Relevant error handling rules
None of the 15 numbered runtime error rules apply directly to migration scripts.
Operational constraint from Deployment: a migration failure must abort
(`alembic upgrade head` is run before `python bot.py`; a non-zero exit stops the
deploy). Do not swallow migration errors.

## Test cases
The spec defines no `tests/test_migrations.py`. The acceptance gate is a
schema round-trip against a disposable Postgres (Docker), never the production
DB. Verify:

- `alembic upgrade head` creates all three tables with the exact columns, types,
  and constraints above, plus all five indexes.
- `users.language` is NOT NULL with DEFAULT `'en'`.
- `conversations.user_id` FK is ON DELETE CASCADE.
- All timestamp columns are TIMESTAMPTZ.
- `alembic downgrade base` cleanly reverses both migrations (no leftover tables
  or indexes).
- The env reads `DATABASE_URL` from `config` and rewrites it to
  `postgresql+psycopg2://`.

## Expected output
- A `migrations/` directory with Alembic config, `env.py`, `001_init.py`, and
  `002_add_indexes.py` implementing the contract exactly
- A verified `upgrade head` → `downgrade base` round-trip on a test database
- After it works: append a `## migrations/` section to `interfaces.md` recording
  the revision ids, the table/column shape, and the index list, so `db.py`
  (module 7) and `tests/test_db.py` can rely on the schema

## Agent instructions
1. Read `CLAUDE.md`, the migrations contract in `technical-spec.md`, and
   `interfaces.md` before writing anything.
2. Write `001_init.py` and `002_add_indexes.py` to satisfy the schema exactly;
   set `002`'s `down_revision` to `001`.
3. Run `alembic upgrade head` then `alembic downgrade base` against a disposable
   Postgres (Docker per the spec) and confirm the round-trip is clean.
4. Match the schema EXACTLY — column names, types, NOT NULL/DEFAULT, the CASCADE
   FK, and all five indexes.
5. Use psycopg2 for migrations only — do not import asyncpg. Read `DATABASE_URL`
   from `config`, never from `os.environ`.
6. Do not add dependencies not already in the project.
7. Do not modify any other module and do not create or stub `db.py`.
8. When the round-trip passes, append the schema summary to `interfaces.md`.
9. If the contract is ambiguous or conflicts with an interface, STOP and ask —
   do not guess.
10. Activate `.venv` before any `alembic`/`python`/`pip` command.
