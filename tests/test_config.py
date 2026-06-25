import importlib
import sys

import pytest

REQUIRED_VARS = {
    "TELEGRAM_BOT_TOKEN": "telegram-token",
    "NEURALDEEP_API_KEY": "neuraldeep-key",
    "NEURALDEEP_API_URL": "https://api.neuraldeep.ru/v1",
    "DATABASE_URL": "postgresql://user:pass@host/db",
    "SECRET_KEY": "secret-key",
    "LOG_LEVEL": "INFO",
}


def _reload_config(monkeypatch, env, clear_required=None):
    # Neutralize .env autoloading so these tests depend ONLY on the env vars
    # set here — never on a developer's local .env file. Patched before the
    # reimport so config's `from dotenv import load_dotenv` picks up the no-op.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    if clear_required:
        for key in clear_required:
            monkeypatch.delenv(key, raising=False)
    sys.modules.pop("config", None)
    return importlib.import_module("config")


def test_all_required_vars_set_import_succeeds(monkeypatch):
    config = _reload_config(monkeypatch, REQUIRED_VARS)
    assert config.TELEGRAM_BOT_TOKEN == "telegram-token"
    assert config.NEURALDEEP_API_KEY == "neuraldeep-key"
    assert config.NEURALDEEP_API_URL == "https://api.neuraldeep.ru/v1"
    assert config.DATABASE_URL == "postgresql://user:pass@host/db"
    assert config.SECRET_KEY == "secret-key"
    assert config.LOG_LEVEL == "INFO"


@pytest.mark.parametrize("missing_var", sorted(REQUIRED_VARS))
def test_missing_required_var_raises_key_error(monkeypatch, missing_var):
    with pytest.raises(KeyError):
        _reload_config(monkeypatch, REQUIRED_VARS, clear_required=[missing_var])


def test_optional_vars_absent_use_defaults(monkeypatch):
    config = _reload_config(monkeypatch, REQUIRED_VARS)
    assert config.MCP_TOOLS_TTL == 86400
    assert config.MAX_HISTORY_MESSAGES == 20
    assert config.CONVERSATION_RETENTION_DAYS == 7
    assert config.LOG_RETENTION_DAYS == 30


def test_optional_vars_set_as_string_numbers_parsed_as_int(monkeypatch):
    env = dict(REQUIRED_VARS)
    env.update(
        {
            "MCP_TOOLS_TTL": "100",
            "MAX_HISTORY_MESSAGES": "5",
            "CONVERSATION_RETENTION_DAYS": "14",
            "LOG_RETENTION_DAYS": "60",
        }
    )
    config = _reload_config(monkeypatch, env)
    assert config.MCP_TOOLS_TTL == 100
    assert isinstance(config.MCP_TOOLS_TTL, int)
    assert config.MAX_HISTORY_MESSAGES == 5
    assert config.CONVERSATION_RETENTION_DAYS == 14
    assert config.LOG_RETENTION_DAYS == 60
