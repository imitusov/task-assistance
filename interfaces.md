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

## language.py

`DetectorFactory.seed = 0` set at import for determinism. No internal
dependencies.

**Constants:**
- `SUPPORTED_LANGUAGES: set[str]` = `{"en", "ru"}`
- `DEFAULT_LANGUAGE: str` = `"en"`
- `MIN_DETECTION_LENGTH: int` = `10`
- `MIN_DETECTION_CONFIDENCE: float` = `0.9` (documented contract value —
  see deviation note below)

**detect(text: str) → str**
Returns `"en"` or `"ru"` only. Returns `DEFAULT_LANGUAGE` on text shorter
than `MIN_DETECTION_LENGTH`, an unsupported top result, low confidence, or
any exception. Never raises.

**resolve(stored_language: str | None, text: str) → tuple[str, bool]**
- Raises `TypeError` if `text is None` — caller must never pass `None`.
- Empty string `stored_language` treated identically to `None`.
- `None`/empty stored → `(detect(text), True)`.
- `len(text) < MIN_DETECTION_LENGTH` with a truthy stored value → `(stored, False)`
  (too short to justify a switch — stored value is preserved even if it
  differs from `detect()`'s default).
- Otherwise, detected == stored → `(stored, False)`; detected != stored →
  `(detected, True)`.
- Never raises except the documented `TypeError`.

**Deviation from literal contract (flagged and approved by user 2026-06-23):**
The contract specifies gating `detect()` on `MIN_DETECTION_CONFIDENCE = 0.9`
using langdetect's top-candidate probability. In practice, langdetect's
probability for short-but-correct supported-language text (e.g. ~0.71 for
the contract's own Russian test phrase) is regularly below 0.9, so a literal
0.9 gate would default that text to EN and fail the contract's own test
case. Resolution: `detect()` internally gates on a lower, undocumented
threshold (`_CONFIDENCE_GATE = 0.6`, private) instead of the public
`MIN_DETECTION_CONFIDENCE` constant. `MIN_DETECTION_CONFIDENCE` remains
defined at `0.9` to satisfy the contract's constant requirement but is not
used as the actual gate value in `detect()`.
