import importlib
import sys

import pytest
from cryptography.fernet import Fernet, InvalidToken

REQUIRED_VARS = {
    "TELEGRAM_BOT_TOKEN": "telegram-token",
    "NEURALDEEP_API_KEY": "neuraldeep-key",
    "NEURALDEEP_API_URL": "https://api.neuraldeep.ru/v1",
    "DATABASE_URL": "postgresql://user:pass@host/db",
    "SECRET_KEY": Fernet.generate_key().decode(),
    "LOG_LEVEL": "INFO",
}


@pytest.fixture
def crypto(monkeypatch):
    for key, value in REQUIRED_VARS.items():
        monkeypatch.setenv(key, value)
    sys.modules.pop("config", None)
    sys.modules.pop("crypto", None)
    return importlib.import_module("crypto")


def test_round_trip(crypto):
    plain = "this-is-a-plain-todoist-token"
    encrypted = crypto.encrypt_token(plain)
    assert crypto.decrypt_token(encrypted) == plain


def test_encrypted_output_differs_from_plain(crypto):
    plain = "this-is-a-plain-todoist-token"
    assert crypto.encrypt_token(plain) != plain


def test_encrypt_output_differs_on_each_call(crypto):
    plain = "this-is-a-plain-todoist-token"
    first = crypto.encrypt_token(plain)
    second = crypto.encrypt_token(plain)
    assert first != second


def test_decrypt_invalid_ciphertext_raises_descriptive_value_error(crypto):
    with pytest.raises(ValueError) as exc_info:
        crypto.decrypt_token("invalid_ciphertext")
    assert not isinstance(exc_info.value, InvalidToken)
    assert str(exc_info.value)


def test_plain_token_never_appears_in_exception_message(crypto):
    plain = "super-secret-todoist-token-value"
    encrypted = crypto.encrypt_token(plain)
    corrupted = encrypted[:-4] + "abcd"
    with pytest.raises(ValueError) as exc_info:
        crypto.decrypt_token(corrupted)
    assert plain not in str(exc_info.value)


def test_encrypt_token_raises_clear_error_on_failure(crypto):
    with pytest.raises(ValueError):
        crypto.encrypt_token(None)


def test_round_trip_with_40_char_hex_token(crypto):
    plain = "a" * 40
    assert plain.isalnum() and len(plain) == 40
    encrypted = crypto.encrypt_token(plain)
    assert encrypted != plain
    assert crypto.decrypt_token(encrypted) == plain
