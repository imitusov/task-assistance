import importlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from alembic import command
from alembic.config import Config

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

REQUIRED_VARS = {
    "TELEGRAM_BOT_TOKEN": "telegram-token",
    "NEURALDEEP_API_KEY": "neuraldeep-key",
    "NEURALDEEP_API_URL": "https://api.neuraldeep.ru/v1",
    "DATABASE_URL": TEST_DATABASE_URL or "postgresql://user:pass@host/db",
    "SECRET_KEY": "test-secret-key",
    "LOG_LEVEL": "INFO",
    "MAX_HISTORY_MESSAGES": "20",
}

ALEMBIC_INI = str(Path(__file__).resolve().parent.parent / "alembic.ini")


class _SingleConnPool:
    """Wraps one already-open connection so db.py's `_pool.acquire()` calls
    reuse it instead of touching a real pool, keeping every test inside the
    same outer (rolled-back) transaction."""

    def __init__(self, connection):
        self._connection = connection

    def acquire(self):
        return self

    async def __aenter__(self):
        return self._connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _reset_env(monkeypatch):
    for key, value in REQUIRED_VARS.items():
        monkeypatch.setenv(key, value)
    sys.modules.pop("config", None)


@pytest.fixture(scope="session", autouse=True)
def _migrated_schema():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    for key, value in REQUIRED_VARS.items():
        os.environ.setdefault(key, value)
    sys.modules.pop("config", None)
    cfg = Config(ALEMBIC_INI)
    command.upgrade(cfg, "head")
    yield
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    sys.modules.pop("config", None)
    command.downgrade(cfg, "base")


@pytest.fixture
def db(monkeypatch):
    _reset_env(monkeypatch)
    sys.modules.pop("db", None)
    return importlib.import_module("db")


@pytest.fixture
async def conn():
    connection = await asyncpg.connect(TEST_DATABASE_URL)
    tx = connection.transaction()
    await tx.start()
    try:
        yield connection
    finally:
        await tx.rollback()
        await connection.close()


@pytest.fixture
def db_conn(db, conn):
    db._pool = _SingleConnPool(conn)
    yield db
    db._pool = None


async def _insert_user(conn, user_id, language="en"):
    await conn.execute(
        "INSERT INTO users (telegram_user_id, language) VALUES ($1, $2)",
        user_id,
        language,
    )


# --- init_pool / close_pool -------------------------------------------------


async def test_init_pool_creates_pool_with_min_and_max_size(db):
    fake_pool = object()
    with patch(
        "db.asyncpg.create_pool", new=AsyncMock(return_value=fake_pool)
    ) as mock_create_pool:
        await db.init_pool()
        mock_create_pool.assert_called_once_with(
            dsn=db.config.DATABASE_URL, min_size=2, max_size=10
        )
        assert db._pool is fake_pool
    db._pool = None


async def test_init_pool_raises_on_failure(db):
    with patch(
        "db.asyncpg.create_pool",
        new=AsyncMock(side_effect=OSError("connection refused")),
    ):
        with pytest.raises(OSError):
            await db.init_pool()


async def test_close_pool_closes_and_clears_pool(db):
    fake_pool = AsyncMock()
    db._pool = fake_pool
    await db.close_pool()
    fake_pool.close.assert_called_once()
    assert db._pool is None


async def test_close_pool_is_safe_when_no_pool_exists(db):
    db._pool = None
    await db.close_pool()
    assert db._pool is None


# --- get_user / save_user ---------------------------------------------


async def test_get_user_returns_none_for_unknown_user(db_conn):
    assert await db_conn.get_user(999999) is None


async def test_save_user_then_get_user_returns_row(db_conn):
    await db_conn.save_user(1, "enc-token", "en")
    row = await db_conn.get_user(1)
    assert row["telegram_user_id"] == 1
    assert row["todoist_token"] == "enc-token"
    assert row["language"] == "en"
    assert row["created_at"] is not None
    assert row["last_active_at"] is not None


async def test_save_user_twice_updates_token_and_language(db_conn):
    await db_conn.save_user(1, "enc-token-1", "en")
    await db_conn.save_user(1, "enc-token-2", "ru")
    row = await db_conn.get_user(1)
    assert row["todoist_token"] == "enc-token-2"
    assert row["language"] == "ru"


async def test_save_user_conflict_updates_last_active_at(db_conn, conn):
    await db_conn.save_user(1, "enc-token-1", "en")
    stale = datetime(2000, 1, 1, tzinfo=timezone.utc)
    await conn.execute(
        "UPDATE users SET last_active_at = $1 WHERE telegram_user_id = 1", stale
    )
    await db_conn.save_user(1, "enc-token-2", "ru")
    row = await db_conn.get_user(1)
    assert row["last_active_at"] > stale


# --- update_language -----------------------------------------------------


async def test_update_language_updates_language_and_last_active_at(db_conn, conn):
    await _insert_user(conn, 1, "en")
    stale = datetime(2000, 1, 1, tzinfo=timezone.utc)
    await conn.execute(
        "UPDATE users SET last_active_at = $1, todoist_token = 'enc' WHERE telegram_user_id = 1",
        stale,
    )
    await db_conn.update_language(1, "ru")
    row = await db_conn.get_user(1)
    assert row["language"] == "ru"
    assert row["last_active_at"] > stale


async def test_update_language_does_not_change_token(db_conn, conn):
    await _insert_user(conn, 1, "en")
    await conn.execute(
        "UPDATE users SET todoist_token = 'enc-token' WHERE telegram_user_id = 1"
    )
    await db_conn.update_language(1, "ru")
    row = await db_conn.get_user(1)
    assert row["todoist_token"] == "enc-token"


# --- touch_user ------------------------------------------------------------


async def test_touch_user_updates_only_last_active_at(db_conn, conn):
    await _insert_user(conn, 1, "en")
    stale = datetime(2000, 1, 1, tzinfo=timezone.utc)
    await conn.execute(
        "UPDATE users SET last_active_at = $1, todoist_token = 'enc-token' WHERE telegram_user_id = 1",
        stale,
    )
    await db_conn.touch_user(1)
    row = await db_conn.get_user(1)
    assert row["last_active_at"] > stale
    assert row["language"] == "en"
    assert row["todoist_token"] == "enc-token"


# --- get_all_users / update_token (key rotation) ---------------------------


async def test_get_all_users_returns_empty_list_when_none_exist(db_conn):
    assert await db_conn.get_all_users() == []


async def test_get_all_users_returns_all_saved_rows(db_conn):
    await db_conn.save_user(1, "enc-1", "en")
    await db_conn.save_user(2, "enc-2", "ru")
    rows = await db_conn.get_all_users()
    ids = {row["telegram_user_id"] for row in rows}
    assert ids == {1, 2}


async def test_update_token_changes_only_token(db_conn, conn):
    await _insert_user(conn, 1, "ru")
    await conn.execute(
        "UPDATE users SET todoist_token = 'old-enc' WHERE telegram_user_id = 1"
    )
    stale = datetime(2000, 1, 1, tzinfo=timezone.utc)
    await conn.execute(
        "UPDATE users SET last_active_at = $1 WHERE telegram_user_id = 1", stale
    )
    await db_conn.update_token(1, "new-enc")
    row = await db_conn.get_user(1)
    assert row["todoist_token"] == "new-enc"
    assert row["language"] == "ru"
    assert row["last_active_at"] == stale


# --- save_user_message / save_turn / trim -----------------------------------


async def test_save_user_message_inserts_row_without_trim(db_conn, conn):
    await _insert_user(conn, 1)
    await db_conn.save_user_message(1, {"role": "user", "content": "hi"})
    rows = await conn.fetch("SELECT role FROM conversations WHERE user_id = 1")
    assert len(rows) == 1
    assert rows[0]["role"] == "user"


async def test_save_turn_inserts_assistant_row_and_trims_once(db_conn, conn):
    await _insert_user(conn, 1)
    await db_conn.save_user_message(1, {"role": "user", "content": "hi"})
    await db_conn.save_turn(1, {"role": "assistant", "content": "hello"})
    rows = await conn.fetch(
        "SELECT role FROM conversations WHERE user_id = 1 ORDER BY id"
    )
    assert [r["role"] for r in rows] == ["user", "assistant"]


async def test_save_user_message_never_trims(db_conn, conn):
    # default MAX_HISTORY_MESSAGES (20) is exceeded on purpose — trimming
    # must not happen because save_user_message never trims
    await _insert_user(conn, 1)
    for i in range(25):
        await db_conn.save_user_message(1, {"role": "user", "content": f"u{i}"})
    rows = await conn.fetch("SELECT id FROM conversations WHERE user_id = 1")
    assert len(rows) == 25


async def test_save_turn_trims_to_max_history_messages(db_conn, conn):
    await _insert_user(conn, 1)
    for i in range(25):
        await db_conn.save_user_message(1, {"role": "user", "content": f"u{i}"})
        await db_conn.save_turn(1, {"role": "assistant", "content": f"a{i}"})
    rows = await conn.fetch("SELECT id FROM conversations WHERE user_id = 1")
    assert len(rows) == 20


async def test_save_turn_preserves_most_recent_rows(db_conn, conn):
    await _insert_user(conn, 1)
    for i in range(25):
        await db_conn.save_user_message(1, {"role": "user", "content": f"u{i}"})
        await db_conn.save_turn(1, {"role": "assistant", "content": f"a{i}"})
    history = await db_conn.get_history(1)
    assert len(history) == 20
    assert history[0] == {"role": "user", "content": "u15"}
    assert history[-1] == {"role": "assistant", "content": "a24"}


# --- save_tool_result --------------------------------------------------------


async def test_save_tool_result_inserts_tool_row_without_trim(db_conn, conn):
    await _insert_user(conn, 1)
    await db_conn.save_tool_result(
        1, {"role": "tool", "tool_call_id": "abc", "content": "result"}
    )
    rows = await conn.fetch("SELECT role FROM conversations WHERE user_id = 1")
    assert len(rows) == 1
    assert rows[0]["role"] == "tool"


# --- save_assistant_tool_call -------------------------------------------------


async def test_save_assistant_tool_call_inserts_assistant_row_without_trim(
    db_conn, conn
):
    await _insert_user(conn, 1)
    await db_conn.save_assistant_tool_call(
        1,
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call-1", "type": "function", "function": {}}],
        },
    )
    rows = await conn.fetch("SELECT role FROM conversations WHERE user_id = 1")
    assert len(rows) == 1
    assert rows[0]["role"] == "assistant"


async def test_save_assistant_tool_call_never_trims(db_conn, conn):
    await _insert_user(conn, 1)
    for i in range(25):
        await db_conn.save_assistant_tool_call(
            1, {"role": "assistant", "content": None, "tool_calls": [{"id": f"c{i}"}]}
        )
    rows = await conn.fetch("SELECT id FROM conversations WHERE user_id = 1")
    assert len(rows) == 25


async def test_save_assistant_tool_call_then_save_tool_result_well_formed_history(
    db_conn, conn
):
    await _insert_user(conn, 1)
    await db_conn.save_user_message(1, {"role": "user", "content": "hi"})
    await db_conn.save_assistant_tool_call(
        1,
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call-1", "type": "function", "function": {}}],
        },
    )
    await db_conn.save_tool_result(
        1, {"role": "tool", "tool_call_id": "call-1", "content": "result"}
    )
    await db_conn.save_turn(1, {"role": "assistant", "content": "done"})
    history = await db_conn.get_history(1)
    roles = [m["role"] for m in history]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert history[1].get("tool_calls") is not None


# --- delete_turn_tool_results -------------------------------------------------


async def test_delete_turn_tool_results_deletes_only_tool_rows_since(db_conn, conn):
    await _insert_user(conn, 1)
    await db_conn.save_user_message(1, {"role": "user", "content": "hi"})
    await db_conn.save_tool_result(
        1, {"role": "tool", "tool_call_id": "a", "content": "r1"}
    )
    since_row = await conn.fetchrow(
        "SELECT created_at FROM conversations WHERE user_id = 1 AND role = 'tool'"
    )
    since = since_row["created_at"]
    await db_conn.delete_turn_tool_results(1, since)
    rows = await conn.fetch(
        "SELECT role FROM conversations WHERE user_id = 1 ORDER BY id"
    )
    assert [r["role"] for r in rows] == ["user"]


async def test_delete_turn_tool_results_boundary_is_inclusive(db_conn, conn):
    await _insert_user(conn, 1)
    await db_conn.save_tool_result(
        1, {"role": "tool", "tool_call_id": "a", "content": "r1"}
    )
    since_row = await conn.fetchrow(
        "SELECT created_at FROM conversations WHERE user_id = 1 AND role = 'tool'"
    )
    since = since_row["created_at"]
    await db_conn.delete_turn_tool_results(1, since)
    count = await conn.fetchval(
        "SELECT count(*) FROM conversations WHERE user_id = 1 AND role = 'tool'"
    )
    assert count == 0


async def test_delete_turn_tool_results_does_not_delete_rows_before_since(
    db_conn, conn
):
    await _insert_user(conn, 1)
    await db_conn.save_tool_result(
        1, {"role": "tool", "tool_call_id": "a", "content": "old"}
    )
    since = datetime.now(timezone.utc) + timedelta(hours=1)
    await db_conn.delete_turn_tool_results(1, since)
    count = await conn.fetchval(
        "SELECT count(*) FROM conversations WHERE user_id = 1 AND role = 'tool'"
    )
    assert count == 1


async def test_delete_turn_tool_results_naive_datetime_raises(db_conn):
    with pytest.raises(ValueError):
        await db_conn.delete_turn_tool_results(1, datetime.now())


async def test_delete_turn_tool_results_no_matching_rows_is_safe(db_conn):
    await db_conn.delete_turn_tool_results(1, datetime.now(timezone.utc))


# --- get_history --------------------------------------------------------------


async def test_get_history_returns_empty_for_new_user(db_conn):
    assert await db_conn.get_history(1) == []


async def test_get_history_orders_ascending_and_excludes_system(db_conn, conn):
    await _insert_user(conn, 1)
    await db_conn.save_user_message(1, {"role": "user", "content": "first"})
    await conn.execute(
        "INSERT INTO conversations (user_id, role, content) VALUES ($1, 'system', $2::jsonb)",
        1,
        json.dumps({"role": "system", "content": "sys"}),
    )
    await db_conn.save_turn(1, {"role": "assistant", "content": "second"})
    history = await db_conn.get_history(1)
    assert history == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "second"},
    ]


# --- clear_user_history --------------------------------------------------------


async def test_clear_user_history_returns_zero_when_empty(db_conn):
    assert await db_conn.clear_user_history(1) == 0


async def test_clear_user_history_deletes_all_roles_and_returns_count(
    db_conn, conn
):
    await _insert_user(conn, 1)
    await db_conn.save_user_message(1, {"role": "user", "content": "u"})
    await db_conn.save_tool_result(
        1, {"role": "tool", "tool_call_id": "a", "content": "t"}
    )
    await db_conn.save_turn(1, {"role": "assistant", "content": "a"})
    count = await db_conn.clear_user_history(1)
    assert count == 3
    remaining = await conn.fetchval(
        "SELECT count(*) FROM conversations WHERE user_id = 1"
    )
    assert remaining == 0


# --- cleanup_old_conversations / cleanup_old_logs ------------------------------


async def test_cleanup_old_conversations_deletes_old_and_keeps_new(db_conn, conn):
    await _insert_user(conn, 1)
    old_time = datetime.now(timezone.utc) - timedelta(days=10)
    new_time = datetime.now(timezone.utc)
    await conn.execute(
        "INSERT INTO conversations (user_id, role, content, created_at) "
        "VALUES (1, 'user', $1::jsonb, $2)",
        json.dumps({"role": "user", "content": "old"}),
        old_time,
    )
    await conn.execute(
        "INSERT INTO conversations (user_id, role, content, created_at) "
        "VALUES (1, 'user', $1::jsonb, $2)",
        json.dumps({"role": "user", "content": "new"}),
        new_time,
    )
    deleted = await db_conn.cleanup_old_conversations(7)
    assert deleted == 1
    remaining = await conn.fetchval(
        "SELECT count(*) FROM conversations WHERE user_id = 1"
    )
    assert remaining == 1


async def test_cleanup_old_logs_deletes_old_and_keeps_new(db_conn, conn):
    old_time = datetime.now(timezone.utc) - timedelta(days=40)
    new_time = datetime.now(timezone.utc)
    await conn.execute(
        "INSERT INTO logs (level, event, data, created_at) "
        "VALUES ('INFO', 'old_event', '{}'::jsonb, $1)",
        old_time,
    )
    await conn.execute(
        "INSERT INTO logs (level, event, data, created_at) "
        "VALUES ('INFO', 'new_event', '{}'::jsonb, $1)",
        new_time,
    )
    deleted = await db_conn.cleanup_old_logs(30)
    assert deleted == 1
    remaining = await conn.fetchval(
        "SELECT count(*) FROM logs WHERE event IN ('old_event', 'new_event')"
    )
    assert remaining == 1


# --- log -----------------------------------------------------------------------


async def test_log_inserts_sanitized_row_and_calls_log_stdout(db_conn, conn):
    with patch("db.logger.log_stdout") as mock_log_stdout:
        await db_conn.log(1, "INFO", "user_message", {"token": "secret", "text": "hi"})
    mock_log_stdout.assert_called_once_with(
        "INFO", "user_message", 1, {"token": "secret", "text": "hi"}
    )
    row = await conn.fetchrow(
        "SELECT level, event, data FROM logs WHERE event = 'user_message'"
    )
    assert row["level"] == "INFO"
    assert json.loads(row["data"]) == {"token": "***REDACTED***", "text": "hi"}


async def test_log_does_not_raise_on_database_failure(db_conn):
    class _FailingPool:
        def acquire(self):
            raise RuntimeError("db is down")

    db_conn._pool = _FailingPool()
    with patch("db.logger.log_stdout") as mock_log_stdout:
        await db_conn.log(1, "ERROR", "db_error", {})
    mock_log_stdout.assert_called_once_with("ERROR", "db_error", 1, {})
