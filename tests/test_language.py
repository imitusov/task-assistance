import pytest

from language import (
    DEFAULT_LANGUAGE,
    MIN_DETECTION_CONFIDENCE,
    MIN_DETECTION_LENGTH,
    SUPPORTED_LANGUAGES,
    detect,
    resolve,
)


def test_constants():
    assert SUPPORTED_LANGUAGES == {"en", "ru"}
    assert DEFAULT_LANGUAGE == "en"
    assert MIN_DETECTION_LENGTH == 10
    assert MIN_DETECTION_CONFIDENCE == 0.9


def test_detect_english():
    assert detect("Hello, how are you today") == "en"


def test_detect_russian():
    assert detect("Привет, как дела сегодня") == "ru"


def test_detect_short_text_defaults_to_english():
    assert detect("ok") == "en"


def test_detect_empty_string_defaults_to_english():
    assert detect("") == "en"


def test_detect_garbage_hex_token_defaults_to_english():
    assert detect("9bba1be2b49ca2c9941ecea5cd3d8a0be3069845") == "en"


def test_detect_is_deterministic():
    text = "Hello, how are you today"
    assert detect(text) == detect(text)


def test_resolve_none_stored_returns_detected_and_true():
    assert resolve(None, "Hello world from me") == ("en", True)


def test_resolve_same_as_stored_returns_no_update():
    assert resolve("ru", "Привет мир как дела сегодня") == ("ru", False)


def test_resolve_different_with_thresholds_met_switches():
    assert resolve("en", "Привет мир как дела сегодня") == ("ru", True)


def test_resolve_different_but_too_short_keeps_stored():
    assert resolve("ru", "ok") == ("ru", False)


def test_resolve_empty_stored_treated_as_none():
    assert resolve("", "Hello world today") == ("en", True)


def test_resolve_none_text_raises_type_error():
    with pytest.raises(TypeError):
        resolve("en", None)
