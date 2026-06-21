# Task: Implement `messages.py` (module 2 of 11)

Follow the workflow and rules in `CLAUDE.md`. Implement only this module.
Read `interfaces.md` first to confirm config's actual signatures. `messages` has
no internal dependencies — it imports nothing from other project modules.

## Module contract (Technical Spec.md → messages.py)

Single source of truth for all user-facing strings in EN and RU. No string
hardcoded elsewhere. Single source for command descriptions.

`get(key: str, lang: str, **kwargs) -> str`
- Unknown `key` raises `KeyError`
- Unknown `lang` falls back to `"en"` silently
- `**kwargs` substituted via `.format(**kwargs)`

Note: `please_wait` is the only message sendable before lock acquisition.

### Required keys (each must exist for BOTH "en" and "ru")

| Key | Placeholders | Description |
|---|---|---|
| welcome | — | Onboarding + token instructions + security notice |
| token_accepted | — | Token saved, message deleted |
| token_invalid | — | Token rejected, try again |
| token_network_error | — | Could not reach Todoist |
| token_deletion_failed | — | Could not delete token message |
| token_accidental | — | Accidental token detected and deleted |
| already_registered | — | Already connected |
| unregistered | — | Not set up, use /start |
| rate_limit_session | retry_time | Session limit hit |
| rate_limit_week | retry_time | Week limit hit |
| llm_timeout | — | LLM too slow |
| tool_failure | — | Todoist unreachable |
| reset_prompt | — | Type confirm |
| reset_confirmed | count | History cleared |
| reset_confirmed_empty | — | History already empty |
| reset_cancelled | — | Reset cancelled |
| refresh_confirmed | — | Reconnected to Todoist |
| group_chat_rejected | — | Private chats only |
| help_text | — | Full command list |
| please_wait | — | Processing in progress |
| decrypt_error | — | Re-register with /token |
| send_error | — | Under 100 chars — sent via direct bot.send_message |
| db_error | — | Temporary error, try again |

## Test cases (Technical Spec.md → tests/test_messages.py)

- `get("token_accepted", "en")` returns non-empty English string
- `get("token_accepted", "ru")` returns non-empty Russian string
- `get("rate_limit_session", "en", retry_time="2 hours")` contains "2 hours"
- `get("rate_limit_session", "ru", retry_time="2 часа")` contains "2 часа"
- `get("unknown_key", "en")` raises `KeyError`
- `get("token_accepted", "fr")` returns English string (unknown lang fallback)
- `get("reset_confirmed", "en", count=5)` contains "5"
- All required keys exist for both "en" and "ru" — iterate the full key list,
  assert no `KeyError`
- `len(get("send_error", "en")) < 100`
- `len(get("send_error", "ru")) < 100`

## Expected output

- `messages.py` implementing the contract exactly
- `tests/test_messages.py` covering every case above, all passing
- After tests pass: append `get`'s signature (and any public command-description
  helper) to `interfaces.md`

## Notes

- `send_error` strings MUST be under 100 chars in both languages.
- Do not implement or stub any other module.
- If anything is ambiguous, STOP and ask (see CLAUDE.md).
