import importlib
import json
import sys

import pytest

REQUIRED_VARS = {
    "TELEGRAM_BOT_TOKEN": "telegram-token",
    "NEURALDEEP_API_KEY": "neuraldeep-key",
    "NEURALDEEP_API_URL": "https://api.neuraldeep.ru/v1",
    "DATABASE_URL": "postgresql://user:pass@host/db",
    "SECRET_KEY": "test-secret-key",
    "LOG_LEVEL": "INFO",
}


@pytest.fixture
def logger(monkeypatch):
    for key, value in REQUIRED_VARS.items():
        monkeypatch.setenv(key, value)
    sys.modules.pop("config", None)
    sys.modules.pop("logger", None)
    return importlib.import_module("logger")


def test_sanitize_redacts_token(logger):
    assert logger.sanitize({"token": "abc123"}) == {"token": "***REDACTED***"}


def test_sanitize_redacts_token_case_insensitive(logger):
    assert logger.sanitize({"TOKEN": "abc123"}) == {"TOKEN": "***REDACTED***"}


def test_sanitize_redacts_key_containing_token(logger):
    assert logger.sanitize({"todoist_token": "abc"}) == {
        "todoist_token": "***REDACTED***"
    }


def test_sanitize_leaves_normal_keys_unchanged(logger):
    assert logger.sanitize({"message": "hello"}) == {"message": "hello"}


def test_sanitize_does_not_mutate_input(logger):
    data = {"token": "abc123"}
    logger.sanitize(data)
    assert data == {"token": "abc123"}


def test_sanitize_redacts_only_matching_keys(logger):
    result = logger.sanitize({"api_key": "x", "normal": "y"})
    assert result == {"api_key": "***REDACTED***", "normal": "y"}


def test_sanitize_does_not_recurse_into_nested_dicts(logger):
    result = logger.sanitize({"params": {"token": "secret"}})
    assert result == {"params": {"token": "secret"}}


def test_sanitize_does_not_redact_token_count(logger):
    # token_count contains the "token" substring but is a non-secret numeric
    # metric the spec mandates be logged verbatim (llm_call event).
    assert logger.sanitize({"token_count": 123}) == {"token_count": 123}


def test_sanitize_does_not_redact_token_count_when_none(logger):
    assert logger.sanitize({"token_count": None}) == {"token_count": None}


def test_sanitize_still_redacts_real_token_alongside_token_count(logger):
    result = logger.sanitize({"token_count": 123, "todoist_token": "abc"})
    assert result == {"token_count": 123, "todoist_token": "***REDACTED***"}


def test_log_stdout_writes_single_json_line(logger, capsys):
    logger.log_stdout("INFO", "user_login", 123, {"message": "ok"})
    captured = capsys.readouterr()
    lines = captured.out.strip().splitlines()
    assert len(lines) == 1
    json.loads(lines[0])


def test_log_stdout_output_contains_required_fields(logger, capsys):
    logger.log_stdout("INFO", "user_login", 123, {"message": "ok"})
    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip())
    assert payload["level"] == "INFO"
    assert payload["event"] == "user_login"
    assert payload["user_id"] == 123
    assert "timestamp" in payload


def test_log_stdout_allows_none_user_id(logger, capsys):
    logger.log_stdout("INFO", "startup", None, {})
    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip())
    assert payload["user_id"] is None


def test_log_stdout_respects_log_level(logger, capsys):
    logger.log_stdout("DEBUG", "noisy_event", 123, {})
    captured = capsys.readouterr()
    assert captured.out == ""


def test_log_stdout_sanitizes_data(logger, capsys):
    logger.log_stdout("INFO", "token_saved", 123, {"token": "secret-value"})
    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip())
    assert payload["data"] == {"token": "***REDACTED***"}
