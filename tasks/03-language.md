# Task: Implement `language.py` (module 3 of 11)

Follow the workflow and rules in `CLAUDE.md`. Implement only this module.
Read `interfaces.md` first. `language` has no internal dependencies — it imports
only `langdetect`.

## Module contract (Technical Spec.md → language.py)

`DetectorFactory.seed = 0` at import for determinism.

Constants: `SUPPORTED_LANGUAGES = {"en", "ru"}`, `DEFAULT_LANGUAGE = "en"`,
`MIN_DETECTION_LENGTH = 10`, `MIN_DETECTION_CONFIDENCE = 0.9`

**detect(text: str) -> str**
Returns `"en"` or `"ru"` only. Returns `DEFAULT_LANGUAGE` on short text,
unsupported result, low confidence, or any exception. Never raises.

**resolve(stored_language: str | None, text: str) -> tuple[str, bool]**
- `text` must not be None (caller's responsibility)
- Empty string `stored_language` treated identically to `None` — defensive
  against bad database data
- `True` in second element → caller must call `db.update_language`
- None or empty stored → `(detected, True)`
- Same as stored → `(stored, False)`
- Different and thresholds met → `(detected, True)`
- Different but thresholds not met → `(stored, False)`
- Never raises

## Test cases (Technical Spec.md → tests/test_language.py)

- `detect("Hello, how are you today")` returns `"en"`
- `detect("Привет, как дела сегодня")` returns `"ru"`
- `detect("ok")` returns `"en"` (below `MIN_DETECTION_LENGTH`)
- `detect("")` returns `"en"` (empty string)
- `detect("9bba1be2b49ca2c9941ecea5cd3d8a0be3069845")` returns `"en"`
  (hex token — garbage detection defaults to EN)
- `detect` called twice with same input returns same result (deterministic)
- `resolve(None, "Hello world from me")` returns `("en", True)`
- `resolve("ru", "Привет мир как дела сегодня")` returns `("ru", False)`
- `resolve("en", "Привет мир как дела сегодня")` returns `("ru", True)`
- `resolve("ru", "ok")` returns `("ru", False)` (too short, no switch)
- `resolve("", "Hello world today")` returns `("en", True)` — empty string
  treated same as None (no language set)
- `resolve("en", None)` raises `TypeError` — caller must never pass None as text

## Expected output

- `language.py` implementing the contract exactly
- `tests/test_language.py` covering every case above, all passing
- After tests pass: append `detect`, `resolve`, and the public constants to
  `interfaces.md`

## Notes

- Determinism is part of the contract — `DetectorFactory.seed = 0` at import.
- `detect` and `resolve` never raise except the documented `resolve(..., None)`
  `TypeError`.
- Do not implement or stub any other module.
- If anything is ambiguous, STOP and ask (see CLAUDE.md).
