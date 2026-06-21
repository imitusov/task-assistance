# Dependency Order

Build order for the Todoist Q&A Task Assistant. Derived from the Module
Contracts in `Technical Spec.md` using `dependency-order-guide.md`. Implement
module N only after every module its line lists is complete and its tests pass.
Append each completed module's public signatures to `interfaces.md` before
starting the next.

## Pre-development gates (not modules)

These run before any application code and must exit 0:

- `test_qwen_tools.py` — verify NeuralDeep tool calling + thinking-token behaviour
- `test_mcp_auth.py` — verify Todoist MCP Bearer auth + SSE tool definitions

---

## Layer 1 — Foundation (no internal dependencies)

1. **config** — depends on: (nothing)
2. **messages** — depends on: (nothing)
3. **language** — depends on: (nothing) — `langdetect`, self-contained constants
4. **logger** — depends on: config
5. **crypto** — depends on: config

## Layer 2 — Data

6. **migrations** (`001_init`, `002_add_indexes`) — depends on: config
   — defines schema; must run before `db` tests
7. **db** — depends on: config, logger (schema from migrations)

## Layer 3 — External integrations

8. **mcp** — depends on: config, logger

## Layer 4 — Orchestration / business logic

9. **llm** — depends on: config, logger, messages, db, mcp

## Layer 5 — Entry point and background tasks

10. **bot** — depends on: config, logger, messages, crypto, language, db, mcp, llm
    — includes message/command handlers, heartbeat task, daily cleanup task,
    and `main()` startup/shutdown wiring

## Last — operational scripts

11. **scripts/rotate_key** — depends on: config, crypto, db
