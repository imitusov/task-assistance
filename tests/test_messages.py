import pytest

import messages

REQUIRED_KEYS = [
    "welcome",
    "token_accepted",
    "token_invalid",
    "token_network_error",
    "token_deletion_failed",
    "token_accidental",
    "already_registered",
    "unregistered",
    "rate_limit_session",
    "rate_limit_week",
    "llm_timeout",
    "tool_failure",
    "reset_prompt",
    "reset_confirmed",
    "reset_confirmed_empty",
    "reset_cancelled",
    "refresh_confirmed",
    "group_chat_rejected",
    "help_text",
    "please_wait",
    "decrypt_error",
    "send_error",
    "db_error",
    "tool_confirm_prompt",
    "tool_confirm_cancelled",
]

PLACEHOLDER_KWARGS = {
    "rate_limit_session": {"retry_time": "x"},
    "rate_limit_week": {"retry_time": "x"},
    "reset_confirmed": {"count": 1},
    "tool_confirm_prompt": {"description": "delete-object(id=123)"},
}


def test_token_accepted_en_non_empty():
    result = messages.get("token_accepted", "en")
    assert isinstance(result, str)
    assert len(result) > 0


def test_token_accepted_ru_non_empty():
    result = messages.get("token_accepted", "ru")
    assert isinstance(result, str)
    assert len(result) > 0


def test_rate_limit_session_en_contains_retry_time():
    result = messages.get("rate_limit_session", "en", retry_time="2 hours")
    assert "2 hours" in result


def test_rate_limit_session_ru_contains_retry_time():
    result = messages.get("rate_limit_session", "ru", retry_time="2 часа")
    assert "2 часа" in result


def test_unknown_key_raises_key_error():
    with pytest.raises(KeyError):
        messages.get("unknown_key", "en")


def test_unknown_lang_falls_back_to_en():
    assert messages.get("token_accepted", "fr") == messages.get("token_accepted", "en")


def test_reset_confirmed_contains_count():
    result = messages.get("reset_confirmed", "en", count=5)
    assert "5" in result


def test_tool_confirm_prompt_contains_description():
    result = messages.get(
        "tool_confirm_prompt", "en", description="delete-object(id=123)"
    )
    assert "delete-object(id=123)" in result


@pytest.mark.parametrize("key", REQUIRED_KEYS)
@pytest.mark.parametrize("lang", ["en", "ru"])
def test_all_required_keys_exist_for_both_languages(key, lang):
    kwargs = PLACEHOLDER_KWARGS.get(key, {})
    result = messages.get(key, lang, **kwargs)
    assert isinstance(result, str)
    assert len(result) > 0


def test_send_error_en_under_100_chars():
    assert len(messages.get("send_error", "en")) < 100


def test_send_error_ru_under_100_chars():
    assert len(messages.get("send_error", "ru")) < 100
