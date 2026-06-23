import asyncio
import re
import time
from datetime import datetime, timedelta, timezone

import httpx
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

import config
import crypto
import db
import language
import llm
import logger
import mcp
import messages

STATE_NORMAL = "NORMAL"
STATE_AWAITING_TOKEN = "AWAITING_TOKEN"
STATE_AWAITING_RESET = "AWAITING_RESET"

_MAX_MESSAGE_LENGTH = 4096
_TOKEN_PATTERN = re.compile(r"^[a-zA-Z0-9]{40}$")
_TODOIST_PROJECTS_URL = "https://api.todoist.net/rest/v2/projects"

_HEARTBEAT_INTERVAL_SECONDS = 60
_CLEANUP_HOUR_UTC = 3
_CLEANUP_RETRY_SECONDS = 60
_KEEP_TYPING_INTERVAL_SECONDS = 4


def _is_private(update: Update) -> bool:
    return update.effective_chat.type == "private"


def _looks_like_token(text: str) -> bool:
    return bool(_TOKEN_PATTERN.fullmatch(text.strip()))


def _get_lock(context: ContextTypes.DEFAULT_TYPE) -> asyncio.Lock:
    return context.user_data.setdefault("lock", asyncio.Lock())


async def _reject_if_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if _is_private(update):
        return False
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=messages.get("group_chat_rejected", "en"),
    )
    return True


def _split_into_chunks(text: str, limit: int = _MAX_MESSAGE_LENGTH) -> list[str]:
    chunks = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n\n", 0, limit)
        sep_len = 2
        if split_at == -1:
            split_at = remaining.rfind("\n", 0, limit)
            sep_len = 1
        if split_at == -1:
            chunks.append(remaining[:limit])
            remaining = remaining[limit:]
            continue
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at + sep_len :]
    chunks.append(remaining)
    return chunks


async def _send_with_truncation(telegram_bot, chat_id: int, text: str, lang: str) -> None:
    for chunk in _split_into_chunks(text):
        try:
            await telegram_bot.send_message(chat_id=chat_id, text=chunk)
        except Exception:
            try:
                await telegram_bot.send_message(
                    chat_id=chat_id, text=messages.get("send_error", lang)
                )
            except Exception:
                logger.log_stdout(
                    "ERROR", "send_error", None, {"chat_id": chat_id}
                )
            return


async def _keep_typing(telegram_bot, chat_id: int) -> None:
    try:
        while True:
            await telegram_bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(_KEEP_TYPING_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        raise


async def _cancel_keep_typing(task: asyncio.Task | None) -> None:
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_group(update, context):
        return

    lock = _get_lock(context)
    if lock.locked():
        return

    await lock.acquire()
    turn_start = datetime.now(timezone.utc)
    try:
        await _process_message(update, context, turn_start)
    finally:
        lock.release()


async def _process_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE, turn_start: datetime
) -> None:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    text = update.message.text or ""

    state = context.user_data.get("state", STATE_NORMAL)
    if state == STATE_AWAITING_TOKEN:
        await _handle_token_input(update, context)
        return

    try:
        user = await db.get_user(user_id)
    except Exception as exc:
        lang = language.detect(text)
        await db.log(user_id, "ERROR", "error", {"traceback": str(exc), "context": "get_user"})
        await context.bot.send_message(chat_id=chat_id, text=messages.get("db_error", lang))
        return

    if user is None:
        lang = language.detect(text)
        await context.bot.send_message(chat_id=chat_id, text=messages.get("unregistered", lang))
        return

    if state == STATE_AWAITING_RESET:
        stored_lang = user["language"]
        if text.strip().lower() == "confirm":
            count = await db.clear_user_history(user_id)
            if count > 0:
                reply = messages.get("reset_confirmed", stored_lang, count=count)
            else:
                reply = messages.get("reset_confirmed_empty", stored_lang)
        else:
            reply = messages.get("reset_cancelled", stored_lang)
        context.user_data["state"] = STATE_NORMAL
        await context.bot.send_message(chat_id=chat_id, text=reply)
        return

    lang, changed = language.resolve(user["language"], text)
    if changed:
        await db.update_language(user_id, lang)
    else:
        await db.touch_user(user_id)

    if _looks_like_token(text):
        try:
            await update.message.delete()
        except Exception:
            pass
        await context.bot.send_message(chat_id=chat_id, text=messages.get("token_accidental", lang))
        return

    try:
        token = crypto.decrypt_token(user["todoist_token"])
    except ValueError:
        await context.bot.send_message(chat_id=chat_id, text=messages.get("decrypt_error", lang))
        return

    keep_typing_task = asyncio.create_task(_keep_typing(context.bot, chat_id))
    try:
        answer = await llm.process_message(user_id, text, token, lang, turn_start)
    finally:
        await _cancel_keep_typing(keep_typing_task)

    await _send_with_truncation(context.bot, chat_id, answer, lang)


async def _handle_token_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    text = update.message.text or ""

    lang = context.user_data.get("detected_language")
    if lang is None:
        lang = language.detect(text)

    try:
        await update.message.delete()
    except Exception:
        await context.bot.send_message(
            chat_id=chat_id, text=messages.get("token_deletion_failed", lang)
        )

    plain_token = text.strip()

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                _TODOIST_PROJECTS_URL,
                headers={"Authorization": f"Bearer {plain_token}"},
                timeout=10.0,
            )
    except httpx.HTTPError:
        await context.bot.send_message(
            chat_id=chat_id, text=messages.get("token_network_error", lang)
        )
        return

    if response.status_code != 200:
        await context.bot.send_message(chat_id=chat_id, text=messages.get("token_invalid", lang))
        return

    existing_user = await db.get_user(user_id)
    if existing_user is not None:
        try:
            old_token = crypto.decrypt_token(existing_user["todoist_token"])
            await mcp.evict_cache(old_token)
        except ValueError:
            pass

    encrypted = crypto.encrypt_token(plain_token)
    await db.save_user(user_id, encrypted, lang)
    context.user_data["state"] = STATE_NORMAL
    await db.log(user_id, "INFO", "new_user", {"language": lang})
    context.user_data.pop("detected_language", None)
    await context.bot.send_message(chat_id=chat_id, text=messages.get("token_accepted", lang))


async def _send_or_wait(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Returns True if the lock was contended (please_wait sent, caller must stop)."""
    lock = _get_lock(context)
    if lock.locked():
        lang = context.user_data.get("detected_language", "en")
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text=messages.get("please_wait", lang)
        )
        return True
    return False


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_group(update, context):
        return
    if await _send_or_wait(update, context):
        return

    lock = _get_lock(context)
    await lock.acquire()
    try:
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        text = update.message.text or ""
        detected_lang = language.detect(text)
        context.user_data["detected_language"] = detected_lang

        user = await db.get_user(user_id)
        if user is not None:
            await context.bot.send_message(
                chat_id=chat_id, text=messages.get("already_registered", user["language"])
            )
        else:
            await context.bot.send_message(chat_id=chat_id, text=messages.get("welcome", detected_lang))
            context.user_data["state"] = STATE_AWAITING_TOKEN
    finally:
        lock.release()


async def token_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_group(update, context):
        return
    if await _send_or_wait(update, context):
        return

    lock = _get_lock(context)
    await lock.acquire()
    try:
        chat_id = update.effective_chat.id
        text = update.message.text or ""
        detected_lang = language.detect(text)
        context.user_data["detected_language"] = detected_lang
        await context.bot.send_message(chat_id=chat_id, text=messages.get("welcome", detected_lang))
        context.user_data["state"] = STATE_AWAITING_TOKEN
    finally:
        lock.release()


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_group(update, context):
        return
    if await _send_or_wait(update, context):
        return

    lock = _get_lock(context)
    await lock.acquire()
    try:
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        text = update.message.text or ""

        user = await db.get_user(user_id)
        if user is None:
            await context.bot.send_message(
                chat_id=chat_id, text=messages.get("unregistered", language.detect(text))
            )
            return

        await context.bot.send_message(chat_id=chat_id, text=messages.get("reset_prompt", user["language"]))
        context.user_data["state"] = STATE_AWAITING_RESET
    finally:
        lock.release()


async def refresh_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_group(update, context):
        return
    if await _send_or_wait(update, context):
        return

    lock = _get_lock(context)
    await lock.acquire()
    try:
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        text = update.message.text or ""

        user = await db.get_user(user_id)
        if user is None:
            await context.bot.send_message(
                chat_id=chat_id, text=messages.get("unregistered", language.detect(text))
            )
            return

        old_token = crypto.decrypt_token(user["todoist_token"])
        await mcp.evict_cache(old_token)
        await context.bot.send_message(
            chat_id=chat_id, text=messages.get("refresh_confirmed", user["language"])
        )
    finally:
        lock.release()


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_group(update, context):
        return
    if await _send_or_wait(update, context):
        return

    lock = _get_lock(context)
    await lock.acquire()
    try:
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        text = update.message.text or ""

        user = await db.get_user(user_id)
        lang = user["language"] if user is not None else language.detect(text)
        await context.bot.send_message(chat_id=chat_id, text=messages.get("help_text", lang))
    finally:
        lock.release()


async def _heartbeat(start_time: float) -> None:
    while True:
        await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
        try:
            uptime_seconds = int(time.monotonic() - start_time)
            await db.log(
                None, "INFO", "heartbeat", {"status": "alive", "uptime_seconds": uptime_seconds}
            )
        except Exception:
            pass


def _seconds_until_next_cleanup(now: datetime) -> float:
    target = now.replace(hour=_CLEANUP_HOUR_UTC, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def _daily_cleanup() -> None:
    while True:
        now = datetime.now(timezone.utc)
        await asyncio.sleep(_seconds_until_next_cleanup(now))
        try:
            deleted_conversations = await db.cleanup_old_conversations(
                config.CONVERSATION_RETENTION_DAYS
            )
            deleted_logs = await db.cleanup_old_logs(config.LOG_RETENTION_DAYS)
            await db.log(
                None,
                "INFO",
                "daily_cleanup",
                {"deleted_conversations": deleted_conversations, "deleted_logs": deleted_logs},
            )
        except Exception as exc:
            await db.log(None, "ERROR", "daily_cleanup", {"error": str(exc)})
        await asyncio.sleep(_CLEANUP_RETRY_SECONDS)


async def _post_init(application: Application) -> None:
    try:
        await db.init_pool()
    except Exception as exc:
        logger.log_stdout("ERROR", "error", None, {"traceback": str(exc), "context": "init_pool"})
        raise

    start_time = application.bot_data["start_time"]
    application.bot_data["heartbeat_task"] = asyncio.create_task(_heartbeat(start_time))
    application.bot_data["cleanup_task"] = asyncio.create_task(_daily_cleanup())


async def _post_shutdown(application: Application) -> None:
    try:
        for key in ("heartbeat_task", "cleanup_task"):
            task = application.bot_data.get(key)
            if task is not None:
                task.cancel()
    finally:
        await db.close_pool()


def main() -> None:
    start_time = time.monotonic()
    application = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    application.bot_data["start_time"] = start_time

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("token", token_command))
    application.add_handler(CommandHandler("reset", reset_command))
    application.add_handler(CommandHandler("refresh", refresh_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    application.run_polling()


if __name__ == "__main__":
    main()
