# Task: Implement `crypto.py` (module 5 of 11)

Follow the workflow and rules in `CLAUDE.md`. Implement only this module.
Read `interfaces.md` first to confirm config's actual signatures — `crypto`
depends on `config` (reads `SECRET_KEY`).

## Module contract (Technical Spec.md → crypto.py)

**encrypt_token(plain: str) -> str**
Fernet-encrypts using `SECRET_KEY`. Returns base64 string. Raises a clear error
on failure. Never logs input.

**decrypt_token(encrypted: str) -> str**
Decrypts a Fernet string. Returns plain token. Raises a descriptive error on
failure — NOT the raw `cryptography` exception. The plain token never appears in
any exception message.

## Test cases (Technical Spec.md → tests/test_crypto.py)

- `decrypt_token(encrypt_token(plain))` returns `plain` (round-trip)
- `encrypt_token(plain)` output differs from `plain`
- `encrypt_token` output differs on each call (random IV)
- `decrypt_token("invalid_ciphertext")` raises a descriptive error (not the raw
  cryptography exception)
- Plain token does not appear in any exception message from `decrypt_token`
- Both functions work with a 40-character hex string (realistic Todoist token)

## Expected output

- `crypto.py` implementing the contract exactly
- `tests/test_crypto.py` covering every case above, all passing
- After tests pass: append `encrypt_token` and `decrypt_token` signatures to
  `interfaces.md`

## Notes

- Wrap `cryptography` exceptions in your own descriptive error — do not leak the
  raw exception, and never include the plain token in the message.
- `SECRET_KEY` comes from `config`, never read directly from env.
- Do not implement or stub any other module.
- If anything is ambiguous, STOP and ask (see CLAUDE.md).
