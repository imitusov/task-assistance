import importlib
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.fernet import Fernet

REQUIRED_VARS = {
    "TELEGRAM_BOT_TOKEN": "telegram-token",
    "NEURALDEEP_API_KEY": "neuraldeep-key",
    "NEURALDEEP_API_URL": "https://api.neuraldeep.ru/v1",
    "DATABASE_URL": "postgresql://user:pass@host/db",
    "SECRET_KEY": "AMGNtrwjcl7yerpq5XX9E2cfH-VQodbPVXJqjdOj9ig=",
    "LOG_LEVEL": "INFO",
}

OLD_KEY = Fernet.generate_key().decode()
NEW_KEY = Fernet.generate_key().decode()


def _make_user(user_id: int, plain_token: str, old_key: str = OLD_KEY) -> dict:
    encrypted = Fernet(old_key.encode()).encrypt(plain_token.encode()).decode()
    return {"telegram_user_id": user_id, "todoist_token": encrypted}


@pytest.fixture
def rotate_key(monkeypatch):
    for key, value in REQUIRED_VARS.items():
        monkeypatch.setenv(key, value)
    for name in ("config", "crypto", "db", "scripts.rotate_key"):
        sys.modules.pop(name, None)
    module = importlib.import_module("scripts.rotate_key")

    module.db = MagicMock()
    module.db.init_pool = AsyncMock()
    module.db.close_pool = AsyncMock()
    module.db.get_all_users = AsyncMock(return_value=[])
    module.db.update_token = AsyncMock()

    return module


# --- Round-trip ----------------------------------------------------------


async def test_round_trip_decrypts_under_new_key_not_old(rotate_key):
    plain = "my-secret-todoist-token-value"
    user = _make_user(1, plain)
    rotate_key.db.get_all_users = AsyncMock(return_value=[user])

    await rotate_key.rotate(OLD_KEY, NEW_KEY)

    rotate_key.db.update_token.assert_called_once()
    args = rotate_key.db.update_token.call_args.args
    assert args[0] == 1
    new_encrypted = args[1]

    assert Fernet(NEW_KEY.encode()).decrypt(new_encrypted.encode()).decode() == plain
    with pytest.raises(Exception):
        Fernet(OLD_KEY.encode()).decrypt(new_encrypted.encode())


async def test_update_token_called_once_per_user_with_correct_id(rotate_key):
    users = [_make_user(i, f"token-{i}") for i in range(1, 4)]
    rotate_key.db.get_all_users = AsyncMock(return_value=users)

    await rotate_key.rotate(OLD_KEY, NEW_KEY)

    assert rotate_key.db.update_token.await_count == 3
    called_ids = {c.args[0] for c in rotate_key.db.update_token.call_args_list}
    assert called_ids == {1, 2, 3}


# --- Batching --------------------------------------------------------------


async def test_batching_120_users_processes_in_three_batches(rotate_key, capsys):
    users = [_make_user(i, f"token-{i}") for i in range(1, 121)]
    rotate_key.db.get_all_users = AsyncMock(return_value=users)

    await rotate_key.rotate(OLD_KEY, NEW_KEY)

    assert rotate_key.db.update_token.await_count == 120
    captured = capsys.readouterr()
    batch_lines = [line for line in captured.out.splitlines() if line.startswith("Batch")]
    assert len(batch_lines) == 3
    assert "1/3" in batch_lines[0]
    assert "2/3" in batch_lines[1]
    assert "3/3" in batch_lines[2]


# --- CLI args ----------------------------------------------------------------


def test_missing_old_key_exits_non_zero(rotate_key):
    with pytest.raises(SystemExit) as exc_info:
        rotate_key.main(["--new-key", NEW_KEY])
    assert exc_info.value.code != 0


def test_missing_new_key_exits_non_zero(rotate_key):
    with pytest.raises(SystemExit) as exc_info:
        rotate_key.main(["--old-key", OLD_KEY])
    assert exc_info.value.code != 0


def test_both_keys_present_parses_successfully(rotate_key):
    args = rotate_key._parse_args(["--old-key", OLD_KEY, "--new-key", NEW_KEY])
    assert args.old_key == OLD_KEY
    assert args.new_key == NEW_KEY


# --- Failure isolation ------------------------------------------------------


async def test_one_user_failure_isolated_others_still_processed(rotate_key):
    good_user_1 = _make_user(1, "token-1")
    bad_user = _make_user(2, "token-2")
    good_user_2 = _make_user(3, "token-3")
    rotate_key.db.get_all_users = AsyncMock(
        return_value=[good_user_1, bad_user, good_user_2]
    )

    async def fake_update_token(user_id, encrypted):
        if user_id == 2:
            raise RuntimeError("db write failed")

    rotate_key.db.update_token = AsyncMock(side_effect=fake_update_token)

    succeeded, failed = await rotate_key.rotate(OLD_KEY, NEW_KEY)

    assert rotate_key.db.update_token.await_count == 3
    assert succeeded == 2
    assert failed == 1


async def test_decrypt_failure_isolated_logs_user_id_and_traceback(rotate_key, capsys):
    bad_user = {"telegram_user_id": 99, "todoist_token": "not-a-valid-fernet-token"}
    good_user = _make_user(1, "token-1")
    rotate_key.db.get_all_users = AsyncMock(return_value=[bad_user, good_user])

    succeeded, failed = await rotate_key.rotate(OLD_KEY, NEW_KEY)

    assert succeeded == 1
    assert failed == 1
    captured = capsys.readouterr()
    assert "99" in captured.out
    rotate_key.db.update_token.assert_called_once_with(
        1, rotate_key.db.update_token.call_args.args[1]
    )


async def test_remaining_batch_processed_after_failure(rotate_key):
    users = [_make_user(i, f"token-{i}") for i in range(1, 6)]

    async def fake_update_token(user_id, encrypted):
        if user_id == 3:
            raise RuntimeError("boom")

    rotate_key.db.get_all_users = AsyncMock(return_value=users)
    rotate_key.db.update_token = AsyncMock(side_effect=fake_update_token)

    succeeded, failed = await rotate_key.rotate(OLD_KEY, NEW_KEY)

    assert rotate_key.db.update_token.await_count == 5
    assert succeeded == 4
    assert failed == 1


# --- Final summary -----------------------------------------------------------


async def test_final_summary_counts_correct_for_mixed_run(rotate_key, capsys):
    users = [_make_user(i, f"token-{i}") for i in range(1, 5)]

    async def fake_update_token(user_id, encrypted):
        if user_id in (2, 4):
            raise RuntimeError("boom")

    rotate_key.db.get_all_users = AsyncMock(return_value=users)
    rotate_key.db.update_token = AsyncMock(side_effect=fake_update_token)

    succeeded, failed = await rotate_key.rotate(OLD_KEY, NEW_KEY)

    assert succeeded == 2
    assert failed == 2
    captured = capsys.readouterr()
    assert "2" in captured.out and "Succeeded" in captured.out
    assert "Failed" in captured.out


# --- Empty DB ------------------------------------------------------------------


async def test_empty_db_no_batches_clean_summary(rotate_key, capsys):
    rotate_key.db.get_all_users = AsyncMock(return_value=[])

    succeeded, failed = await rotate_key.rotate(OLD_KEY, NEW_KEY)

    assert succeeded == 0
    assert failed == 0
    rotate_key.db.update_token.assert_not_called()
    captured = capsys.readouterr()
    assert "Batch" not in captured.out


# --- No secrets in output ------------------------------------------------------


async def test_no_plaintext_token_or_keys_in_output(rotate_key, capsys):
    plain = "super-secret-plaintext-token-value"
    users = [_make_user(1, plain), {"telegram_user_id": 2, "todoist_token": "garbage"}]
    rotate_key.db.get_all_users = AsyncMock(return_value=users)

    await rotate_key.rotate(OLD_KEY, NEW_KEY)

    captured = capsys.readouterr()
    assert plain not in captured.out
    assert OLD_KEY not in captured.out
    assert NEW_KEY not in captured.out


# --- Pool lifecycle -------------------------------------------------------------


async def test_pool_lifecycle_init_then_close(rotate_key):
    rotate_key.db.get_all_users = AsyncMock(return_value=[])

    await rotate_key.rotate(OLD_KEY, NEW_KEY)

    rotate_key.db.init_pool.assert_called_once()
    rotate_key.db.close_pool.assert_called_once()


async def test_close_pool_called_even_when_fetch_raises(rotate_key):
    rotate_key.db.get_all_users = AsyncMock(side_effect=RuntimeError("db unreachable"))

    with pytest.raises(RuntimeError):
        await rotate_key.rotate(OLD_KEY, NEW_KEY)

    rotate_key.db.close_pool.assert_called_once()


def test_main_invokes_rotate_with_pool_lifecycle(rotate_key, monkeypatch):
    rotate_key.db.get_all_users = AsyncMock(return_value=[])

    rotate_key.main(["--old-key", OLD_KEY, "--new-key", NEW_KEY])

    rotate_key.db.init_pool.assert_called_once()
    rotate_key.db.close_pool.assert_called_once()
