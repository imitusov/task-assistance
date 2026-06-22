# Interfaces

This file records the public surface of every completed module. It is appended
to after each module passes its tests. Read it before implementing any module.

Append one section per module, in the build order defined in
`dependency-order.md`, so the file reads top-to-bottom as the dependency chain.
Record exact signatures (name, typed parameters, return type, async, raised
exceptions, critical constraints) — never record a signature for unfinished
code.

## config.py

Loads all env vars at import time (module-level side effect, per CLAUDE.md
exception). Required vars raise `KeyError` at import if missing — never
silently defaulted.

**Required (str, no default — `KeyError` if unset):**
- `TELEGRAM_BOT_TOKEN: str`
- `NEURALDEEP_API_KEY: str`
- `NEURALDEEP_API_URL: str`
- `DATABASE_URL: str`
- `SECRET_KEY: str`
- `LOG_LEVEL: str`

**Optional (int, parsed from env string, with defaults):**
- `MCP_TOOLS_TTL: int` — default `86400`
- `MAX_HISTORY_MESSAGES: int` — default `20`
- `CONVERSATION_RETENTION_DAYS: int` — default `7`
- `LOG_RETENTION_DAYS: int` — default `30`

All modules must import these names from `config` — never read
`os.environ` directly.

## messages.py

Single source of truth for all user-facing strings (EN and RU) and command
descriptions. No internal dependencies.

**get(key: str, lang: str, \*\*kwargs: object) → str**
- Unknown `key` → raises `KeyError` (checked against the resolved language's
  dict, after fallback)
- Unknown `lang` → falls back to `"en"` silently
- `**kwargs` substituted into the template via `str.format(**kwargs)`
- `please_wait` is the only message sendable before lock acquisition (per
  contract note — no special handling inside `get` itself)

**Required keys (present for both `"en"` and `"ru"`):**
`welcome`, `token_accepted`, `token_invalid`, `token_network_error`,
`token_deletion_failed`, `token_accidental`, `already_registered`,
`unregistered`, `rate_limit_session` (`retry_time`), `rate_limit_week`
(`retry_time`), `llm_timeout`, `tool_failure`, `reset_prompt`,
`reset_confirmed` (`count`), `reset_confirmed_empty`, `reset_cancelled`,
`refresh_confirmed`, `group_chat_rejected`, `help_text`, `please_wait`,
`decrypt_error`, `send_error`, `db_error`.

**Constraint:** `send_error` is < 100 chars in both languages (enforced by
test, not by `get` itself — callers must not assume truncation).

No public command-description helper beyond `help_text` was required by the
contract for this module.

## crypto.py

Fernet symmetric encryption/decryption of Todoist tokens, keyed by
`config.SECRET_KEY`. No module-level side effects — `Fernet` is instantiated
inside each function call, not cached at import time.

**encrypt_token(plain: str) → str**
- Fernet-encrypts `plain` using `config.SECRET_KEY`, returns the base64
  ciphertext as `str`
- On any failure (including non-`str` input), raises `ValueError` with a
  fixed descriptive message — never the raw `cryptography` exception, never
  includes `plain` in the message
- Output differs from `plain` and is non-deterministic across calls (random
  IV/nonce per Fernet encryption)

**decrypt_token(encrypted: str) → str**
- Decrypts a Fernet ciphertext produced by `encrypt_token`, returns the
  original plain `str`
- On any failure (invalid/corrupted ciphertext, wrong key, etc.), raises
  `ValueError` with a fixed descriptive message — never `InvalidToken` or any
  other raw `cryptography` exception, and never includes the plain token in
  the message
- Callers (`bot.py`) catch `ValueError` specifically to send `decrypt_error`
