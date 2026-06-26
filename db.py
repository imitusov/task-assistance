import json
from datetime import datetime

import asyncpg

import config
import logger

_pool: asyncpg.Pool | None = None


async def init_pool() -> None:
    global _pool
    _pool = await asyncpg.create_pool(
        dsn=config.DATABASE_URL, min_size=2, max_size=10
    )


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def _deleted_count(status: str) -> int:
    return int(status.split()[-1])


# --- Users -------------------------------------------------------------


async def get_user(user_id: int) -> dict | None:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT telegram_user_id, todoist_token, language, created_at, "
            "last_active_at FROM users WHERE telegram_user_id = $1",
            user_id,
        )
    return dict(row) if row is not None else None


async def save_user(user_id: int, encrypted_token: str, language: str) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (telegram_user_id, todoist_token, language, last_active_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (telegram_user_id) DO UPDATE SET
                todoist_token = EXCLUDED.todoist_token,
                language = EXCLUDED.language,
                last_active_at = NOW()
            """,
            user_id,
            encrypted_token,
            language,
        )


async def update_language(user_id: int, language: str) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET language = $2, last_active_at = NOW() "
            "WHERE telegram_user_id = $1",
            user_id,
            language,
        )


async def touch_user(user_id: int) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET last_active_at = NOW() WHERE telegram_user_id = $1",
            user_id,
        )


async def get_all_users() -> list[dict]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT telegram_user_id, todoist_token, language, created_at, "
            "last_active_at FROM users"
        )
    return [dict(row) for row in rows]


async def update_token(user_id: int, encrypted_token: str) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET todoist_token = $2 WHERE telegram_user_id = $1",
            user_id,
            encrypted_token,
        )


# --- Conversations -------------------------------------------------------


async def get_history(user_id: int) -> list[dict]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT content FROM conversations WHERE user_id = $1 "
            "AND role != 'system' ORDER BY created_at ASC, id ASC",
            user_id,
        )
    return [json.loads(row["content"]) for row in rows]


async def save_user_message(user_id: int, content: dict) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO conversations (user_id, role, content) "
            "VALUES ($1, $2, $3::jsonb)",
            user_id,
            content["role"],
            json.dumps(content),
        )


async def save_turn(user_id: int, assistant_content: dict) -> None:
    async with _pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO conversations (user_id, role, content) "
                "VALUES ($1, $2, $3::jsonb)",
                user_id,
                assistant_content["role"],
                json.dumps(assistant_content),
            )
            await conn.execute(
                """
                DELETE FROM conversations
                WHERE user_id = $1
                AND id NOT IN (
                    SELECT id FROM conversations
                    WHERE user_id = $1
                    ORDER BY created_at DESC, id DESC
                    LIMIT $2
                )
                """,
                user_id,
                config.MAX_HISTORY_MESSAGES,
            )


async def save_tool_result(user_id: int, tool_content: dict) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO conversations (user_id, role, content) "
            "VALUES ($1, $2, $3::jsonb)",
            user_id,
            tool_content["role"],
            json.dumps(tool_content),
        )


async def save_assistant_tool_call(user_id: int, content: dict) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO conversations (user_id, role, content) "
            "VALUES ($1, $2, $3::jsonb)",
            user_id,
            content["role"],
            json.dumps(content),
        )


async def delete_turn_tool_results(user_id: int, since: datetime) -> None:
    if since.tzinfo is None:
        raise ValueError("delete_turn_tool_results requires a timezone-aware datetime")
    async with _pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM conversations WHERE user_id = $1 AND role = 'tool' "
            "AND created_at >= $2",
            user_id,
            since,
        )


async def clear_user_history(user_id: int) -> int:
    async with _pool.acquire() as conn:
        status = await conn.execute(
            "DELETE FROM conversations WHERE user_id = $1", user_id
        )
    return _deleted_count(status)


async def cleanup_old_conversations(days: int) -> int:
    async with _pool.acquire() as conn:
        status = await conn.execute(
            "DELETE FROM conversations WHERE created_at < NOW() - make_interval(days => $1)",
            days,
        )
    return _deleted_count(status)


async def cleanup_old_logs(days: int) -> int:
    async with _pool.acquire() as conn:
        status = await conn.execute(
            "DELETE FROM logs WHERE created_at < NOW() - make_interval(days => $1)",
            days,
        )
    return _deleted_count(status)


# --- Logging -------------------------------------------------------------


async def log(user_id: int | None, level: str, event: str, data: dict) -> None:
    sanitized = logger.sanitize(data)
    try:
        async with _pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO logs (user_id, level, event, data) "
                "VALUES ($1, $2, $3, $4::jsonb)",
                user_id,
                level,
                event,
                json.dumps(sanitized),
            )
    except Exception:
        pass
    logger.log_stdout(level, event, user_id, data)
