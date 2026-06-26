import asyncio
import importlib
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

REQUIRED_VARS = {
    "TELEGRAM_BOT_TOKEN": "telegram-token",
    "NEURALDEEP_API_KEY": "neuraldeep-key",
    "NEURALDEEP_API_URL": "https://api.neuraldeep.ru/v1",
    "DATABASE_URL": "postgresql://user:pass@host/db",
    "SECRET_KEY": "AMGNtrwjcl7yerpq5XX9E2cfH-VQodbPVXJqjdOj9ig=",
    "LOG_LEVEL": "INFO",
    "CONVERSATION_RETENTION_DAYS": "7",
    "LOG_RETENTION_DAYS": "30",
}


@pytest.fixture
def bot(monkeypatch):
    for key, value in REQUIRED_VARS.items():
        monkeypatch.setenv(key, value)
    for name in ("config", "messages", "crypto", "language", "logger", "db", "mcp", "llm", "bot"):
        sys.modules.pop(name, None)
    module = importlib.import_module("bot")

    module.db = MagicMock()
    module.db.get_user = AsyncMock(return_value=None)
    module.db.save_user = AsyncMock()
    module.db.update_language = AsyncMock()
    module.db.touch_user = AsyncMock()
    module.db.clear_user_history = AsyncMock(return_value=0)
    module.db.cleanup_old_conversations = AsyncMock(return_value=0)
    module.db.cleanup_old_logs = AsyncMock(return_value=0)
    module.db.log = AsyncMock()
    module.db.init_pool = AsyncMock()
    module.db.close_pool = AsyncMock()

    module.mcp = MagicMock()
    module.mcp.evict_cache = AsyncMock()

    real_llm = sys.modules["llm"]
    module.llm = MagicMock()
    module.llm.process_message = AsyncMock(return_value="answer")
    module.llm.resume_tool_call = AsyncMock(return_value="answer")
    module.llm.ConfirmationRequired = real_llm.ConfirmationRequired

    return module


def _make_update(text=None, chat_type="private", user_id=1, chat_id=1, message_id=99):
    update = MagicMock()
    update.effective_chat.type = chat_type
    update.effective_chat.id = chat_id
    update.effective_user.id = user_id
    update.message.text = text
    update.message.message_id = message_id
    update.message.delete = AsyncMock()
    return update


def _make_context(user_data=None):
    context = MagicMock()
    context.user_data = user_data if user_data is not None else {}
    context.bot.send_message = AsyncMock()
    context.bot.send_chat_action = AsyncMock()
    context.bot.delete_message = AsyncMock()
    return context


def _make_user(bot_module, language="en", plain_token="x" * 40):
    return {
        "telegram_user_id": 1,
        "todoist_token": bot_module.crypto.encrypt_token(plain_token),
        "language": language,
        "created_at": None,
        "last_active_at": None,
    }


def _patch_token_validation(bot_module, monkeypatch, status_code=200, get_side_effect=None):
    """Mocks the httpx.AsyncClient used by _handle_token_input's GET to the
    Todoist projects endpoint. Pass get_side_effect for network-error cases."""
    client_instance = AsyncMock()
    if get_side_effect is not None:
        client_instance.get = AsyncMock(side_effect=get_side_effect)
    else:
        response = httpx.Response(status_code, request=httpx.Request("GET", "https://x"))
        client_instance.get = AsyncMock(return_value=response)
    client_instance.__aenter__ = AsyncMock(return_value=client_instance)
    client_instance.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(bot_module.httpx, "AsyncClient", MagicMock(return_value=client_instance))
    return client_instance


# --- Group chat guard --------------------------------------------------------


async def test_group_message_sends_group_chat_rejected_no_processing(bot):
    update = _make_update(text="hello", chat_type="group")
    context = _make_context()

    await bot.message_handler(update, context)

    context.bot.send_message.assert_called_once()
    assert context.bot.send_message.call_args.kwargs["text"] == bot.messages.get(
        "group_chat_rejected", "en"
    )
    bot.db.get_user.assert_not_called()


async def test_private_message_processing_continues(bot):
    update = _make_update(text="hello there friend")
    context = _make_context()
    bot.db.get_user = AsyncMock(return_value=None)

    await bot.message_handler(update, context)

    bot.db.get_user.assert_called_once()


async def test_group_command_sends_group_chat_rejected(bot):
    update = _make_update(text="/start", chat_type="group")
    context = _make_context()

    await bot.start_command(update, context)

    context.bot.send_message.assert_called_once()
    assert context.bot.send_message.call_args.kwargs["text"] == bot.messages.get(
        "group_chat_rejected", "en"
    )


# --- Per-user lock -------------------------------------------------------------


async def test_second_concurrent_message_while_locked_discarded_silently(bot):
    update = _make_update(text="hello there friend")
    context = _make_context()
    lock = asyncio.Lock()
    await lock.acquire()
    context.user_data["lock"] = lock

    await bot.message_handler(update, context)

    context.bot.send_message.assert_not_called()
    bot.db.get_user.assert_not_called()


async def test_second_concurrent_command_while_locked_sends_please_wait(bot):
    update = _make_update(text="/start")
    context = _make_context()
    lock = asyncio.Lock()
    await lock.acquire()
    context.user_data["lock"] = lock

    await bot.start_command(update, context)

    context.bot.send_message.assert_called_once()
    assert context.bot.send_message.call_args.kwargs["text"] == bot.messages.get(
        "please_wait", "en"
    )


async def test_lock_released_after_successful_handler(bot):
    update = _make_update(text="hello there friend")
    context = _make_context()
    bot.db.get_user = AsyncMock(return_value=None)

    await bot.message_handler(update, context)

    assert context.user_data["lock"].locked() is False


async def test_lock_released_when_handler_raises(bot):
    update = _make_update(text="hello there friend")
    context = _make_context()
    bot.db.get_user = AsyncMock(return_value=_make_user(bot))
    bot.llm.process_message = AsyncMock(side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError):
        await bot.message_handler(update, context)

    assert context.user_data["lock"].locked() is False


async def test_keep_typing_cancelled_when_process_message_raises(bot, monkeypatch):
    cancelled = {"value": False}

    async def fake_keep_typing(telegram_bot, chat_id):
        try:
            await asyncio.sleep(1000)
        except asyncio.CancelledError:
            cancelled["value"] = True
            raise

    monkeypatch.setattr(bot, "_keep_typing", fake_keep_typing)
    update = _make_update(text="hello there friend")
    context = _make_context()
    bot.db.get_user = AsyncMock(return_value=_make_user(bot))

    async def _raise_after_yield(*args, **kwargs):
        await asyncio.sleep(0)
        raise RuntimeError("boom")

    bot.llm.process_message = _raise_after_yield

    with pytest.raises(RuntimeError):
        await bot.message_handler(update, context)

    assert cancelled["value"] is True


# --- State machine: individual transitions -----------------------------------


async def test_normal_state_runs_message_handler_path(bot):
    update = _make_update(text="hello there friend")
    context = _make_context()
    bot.db.get_user = AsyncMock(return_value=_make_user(bot))

    await bot.message_handler(update, context)

    bot.llm.process_message.assert_called_once()


async def test_awaiting_token_state_routes_to_token_handler(bot, monkeypatch):
    update = _make_update(text="a" * 40)
    context = _make_context(user_data={"state": "AWAITING_TOKEN"})
    _patch_token_validation(bot, monkeypatch)

    await bot.message_handler(update, context)

    bot.db.save_user.assert_called_once()
    bot.llm.process_message.assert_not_called()


async def test_awaiting_reset_confirm_clears_history_state_normal(bot):
    update = _make_update(text="confirm")
    context = _make_context(user_data={"state": "AWAITING_RESET"})
    bot.db.get_user = AsyncMock(return_value=_make_user(bot))
    bot.db.clear_user_history = AsyncMock(return_value=3)

    await bot.message_handler(update, context)

    bot.db.clear_user_history.assert_called_once()
    assert context.user_data["state"] == "NORMAL"


async def test_awaiting_reset_confirm_empty_history_sends_empty_message(bot):
    update = _make_update(text="confirm")
    context = _make_context(user_data={"state": "AWAITING_RESET"})
    bot.db.get_user = AsyncMock(return_value=_make_user(bot))
    bot.db.clear_user_history = AsyncMock(return_value=0)

    await bot.message_handler(update, context)

    assert context.bot.send_message.call_args.kwargs["text"] == bot.messages.get(
        "reset_confirmed_empty", "en"
    )
    assert context.user_data["state"] == "NORMAL"


async def test_awaiting_reset_confirm_case_insensitive(bot):
    update = _make_update(text="CONFIRM")
    context = _make_context(user_data={"state": "AWAITING_RESET"})
    bot.db.get_user = AsyncMock(return_value=_make_user(bot))
    bot.db.clear_user_history = AsyncMock(return_value=3)

    await bot.message_handler(update, context)

    bot.db.clear_user_history.assert_called_once()
    assert context.user_data["state"] == "NORMAL"


async def test_awaiting_reset_other_text_cancelled_state_normal(bot):
    update = _make_update(text="nevermind")
    context = _make_context(user_data={"state": "AWAITING_RESET"})
    bot.db.get_user = AsyncMock(return_value=_make_user(bot))

    await bot.message_handler(update, context)

    bot.db.clear_user_history.assert_not_called()
    assert context.user_data["state"] == "NORMAL"
    assert context.bot.send_message.call_args.kwargs["text"] == bot.messages.get(
        "reset_cancelled", "en"
    )


# --- Destructive-action confirmation ----------------------------------------


async def test_confirmation_required_sets_state_sends_prompt_stores_pending(bot):
    update = _make_update(text="delete that task")
    context = _make_context()
    bot.db.get_user = AsyncMock(return_value=_make_user(bot))
    pending_context = {"tool_calls": [{"id": "call-1"}]}
    bot.llm.process_message = AsyncMock(
        return_value=bot.llm.ConfirmationRequired(
            description="delete-object(id=123)", context=pending_context
        )
    )

    await bot.message_handler(update, context)

    assert context.user_data["state"] == "AWAITING_TOOL_CONFIRM"
    assert context.user_data["pending_tool_confirm"] == pending_context
    assert context.bot.send_message.call_args.kwargs["text"] == bot.messages.get(
        "tool_confirm_prompt", "en", description="delete-object(id=123)"
    )
    bot.llm.resume_tool_call.assert_not_called()


async def test_awaiting_tool_confirm_confirm_executes_pending_state_normal(bot):
    pending_context = {"tool_calls": [{"id": "call-1"}]}
    update = _make_update(text="confirm")
    context = _make_context(
        user_data={
            "state": "AWAITING_TOOL_CONFIRM",
            "pending_tool_confirm": pending_context,
        }
    )
    bot.db.get_user = AsyncMock(return_value=_make_user(bot))
    bot.llm.resume_tool_call = AsyncMock(return_value="Deleted.")

    await bot.message_handler(update, context)

    bot.llm.resume_tool_call.assert_awaited_once()
    call_args = bot.llm.resume_tool_call.call_args.args
    assert call_args[-1] == pending_context
    assert context.user_data["state"] == "NORMAL"
    assert "pending_tool_confirm" not in context.user_data
    assert context.bot.send_message.call_args.kwargs["text"] == "Deleted."


async def test_awaiting_tool_confirm_case_insensitive(bot):
    pending_context = {"tool_calls": [{"id": "call-1"}]}
    update = _make_update(text="CONFIRM")
    context = _make_context(
        user_data={
            "state": "AWAITING_TOOL_CONFIRM",
            "pending_tool_confirm": pending_context,
        }
    )
    bot.db.get_user = AsyncMock(return_value=_make_user(bot))
    bot.llm.resume_tool_call = AsyncMock(return_value="Deleted.")

    await bot.message_handler(update, context)

    bot.llm.resume_tool_call.assert_awaited_once()
    assert context.user_data["state"] == "NORMAL"


async def test_awaiting_tool_confirm_other_text_cancels(bot):
    pending_context = {"tool_calls": [{"id": "call-1"}]}
    update = _make_update(text="nevermind")
    context = _make_context(
        user_data={
            "state": "AWAITING_TOOL_CONFIRM",
            "pending_tool_confirm": pending_context,
        }
    )
    bot.db.get_user = AsyncMock(return_value=_make_user(bot))

    await bot.message_handler(update, context)

    bot.llm.resume_tool_call.assert_not_called()
    assert context.user_data["state"] == "NORMAL"
    assert "pending_tool_confirm" not in context.user_data
    assert context.bot.send_message.call_args.kwargs["text"] == bot.messages.get(
        "tool_confirm_cancelled", "en"
    )


async def test_awaiting_tool_confirm_decrypt_error_sends_decrypt_error(bot):
    pending_context = {"tool_calls": [{"id": "call-1"}]}
    update = _make_update(text="confirm")
    context = _make_context(
        user_data={
            "state": "AWAITING_TOOL_CONFIRM",
            "pending_tool_confirm": pending_context,
        }
    )
    user = _make_user(bot)
    user["todoist_token"] = "not-valid-encrypted-data"
    bot.db.get_user = AsyncMock(return_value=user)

    await bot.message_handler(update, context)

    bot.llm.resume_tool_call.assert_not_called()
    assert context.user_data["state"] == "NORMAL"
    assert context.bot.send_message.call_args.kwargs["text"] == bot.messages.get(
        "decrypt_error", "en"
    )


async def test_confirmation_required_result_chains_to_another_confirmation(bot):
    update = _make_update(text="confirm")
    pending_context = {"tool_calls": [{"id": "call-1"}]}
    context = _make_context(
        user_data={
            "state": "AWAITING_TOOL_CONFIRM",
            "pending_tool_confirm": pending_context,
        }
    )
    bot.db.get_user = AsyncMock(return_value=_make_user(bot))
    next_pending_context = {"tool_calls": [{"id": "call-2"}]}
    bot.llm.resume_tool_call = AsyncMock(
        return_value=bot.llm.ConfirmationRequired(
            description="delete-object(id=456)", context=next_pending_context
        )
    )

    await bot.message_handler(update, context)

    assert context.user_data["state"] == "AWAITING_TOOL_CONFIRM"
    assert context.user_data["pending_tool_confirm"] == next_pending_context
    assert context.bot.send_message.call_args.kwargs["text"] == bot.messages.get(
        "tool_confirm_prompt", "en", description="delete-object(id=456)"
    )


async def test_sequence_destructive_confirm(bot):
    update = _make_update(text="delete that task")
    context = _make_context()
    bot.db.get_user = AsyncMock(return_value=_make_user(bot))
    pending_context = {"tool_calls": [{"id": "call-1"}]}
    bot.llm.process_message = AsyncMock(
        return_value=bot.llm.ConfirmationRequired(
            description="delete-object(id=123)", context=pending_context
        )
    )
    await bot.message_handler(update, context)
    assert context.user_data["state"] == "AWAITING_TOOL_CONFIRM"

    update_confirm = _make_update(text="confirm")
    bot.llm.resume_tool_call = AsyncMock(return_value="Deleted.")
    await bot.message_handler(update_confirm, context)

    assert context.user_data["state"] == "NORMAL"
    bot.llm.resume_tool_call.assert_awaited_once()
    assert context.bot.send_message.call_args.kwargs["text"] == "Deleted."


async def test_start_new_user_sets_awaiting_token(bot):
    update = _make_update(text="/start")
    context = _make_context()
    bot.db.get_user = AsyncMock(return_value=None)

    await bot.start_command(update, context)

    assert context.user_data["state"] == "AWAITING_TOKEN"


async def test_start_existing_user_already_registered_state_unchanged(bot):
    update = _make_update(text="/start")
    context = _make_context()
    bot.db.get_user = AsyncMock(return_value=_make_user(bot))

    await bot.start_command(update, context)

    assert "state" not in context.user_data
    assert context.bot.send_message.call_args.kwargs["text"] == bot.messages.get(
        "already_registered", "en"
    )


async def test_token_command_sets_awaiting_token(bot):
    update = _make_update(text="/token")
    context = _make_context()

    await bot.token_command(update, context)

    assert context.user_data["state"] == "AWAITING_TOKEN"


# --- State machine: sequences -------------------------------------------------


async def test_sequence_start_then_valid_token(bot, monkeypatch):
    update_start = _make_update(text="/start")
    context = _make_context()
    bot.db.get_user = AsyncMock(return_value=None)
    await bot.start_command(update_start, context)
    assert context.user_data["state"] == "AWAITING_TOKEN"

    _patch_token_validation(bot, monkeypatch)

    update_token = _make_update(text="a" * 40)
    await bot.message_handler(update_token, context)

    assert context.user_data["state"] == "NORMAL"
    assert context.bot.send_message.call_args.kwargs["text"] == bot.messages.get(
        "token_accepted", "en"
    )


async def test_sequence_start_invalid_then_valid_token(bot, monkeypatch):
    update_start = _make_update(text="/start")
    context = _make_context()
    bot.db.get_user = AsyncMock(return_value=None)
    await bot.start_command(update_start, context)

    client_instance = _patch_token_validation(bot, monkeypatch, status_code=401)

    update_token = _make_update(text="a" * 40)
    await bot.message_handler(update_token, context)

    assert context.user_data["state"] == "AWAITING_TOKEN"
    assert context.bot.send_message.call_args.kwargs["text"] == bot.messages.get(
        "token_invalid", "en"
    )

    good_response = httpx.Response(200, request=httpx.Request("GET", "https://x"))
    client_instance.get = AsyncMock(return_value=good_response)

    await bot.message_handler(update_token, context)

    assert context.user_data["state"] == "NORMAL"
    assert context.bot.send_message.call_args.kwargs["text"] == bot.messages.get(
        "token_accepted", "en"
    )


async def test_sequence_reset_confirm(bot):
    update_reset = _make_update(text="/reset")
    context = _make_context()
    bot.db.get_user = AsyncMock(return_value=_make_user(bot))
    await bot.reset_command(update_reset, context)
    assert context.user_data["state"] == "AWAITING_RESET"

    update_confirm = _make_update(text="confirm")
    bot.db.clear_user_history = AsyncMock(return_value=1)
    await bot.message_handler(update_confirm, context)

    assert context.user_data["state"] == "NORMAL"
    bot.db.clear_user_history.assert_called_once()


async def test_sequence_reset_other_text(bot):
    update_reset = _make_update(text="/reset")
    context = _make_context()
    bot.db.get_user = AsyncMock(return_value=_make_user(bot))
    await bot.reset_command(update_reset, context)

    update_other = _make_update(text="nope")
    await bot.message_handler(update_other, context)

    assert context.user_data["state"] == "NORMAL"
    assert context.bot.send_message.call_args.kwargs["text"] == bot.messages.get(
        "reset_cancelled", "en"
    )


async def test_sequence_token_network_error_then_valid(bot, monkeypatch):
    update_token_cmd = _make_update(text="/token")
    context = _make_context()
    await bot.token_command(update_token_cmd, context)

    client_instance = _patch_token_validation(
        bot, monkeypatch, get_side_effect=httpx.ConnectError("down")
    )

    update_token = _make_update(text="a" * 40)
    await bot.message_handler(update_token, context)

    assert context.user_data["state"] == "AWAITING_TOKEN"
    assert context.bot.send_message.call_args.kwargs["text"] == bot.messages.get(
        "token_network_error", "en"
    )

    good_response = httpx.Response(200, request=httpx.Request("GET", "https://x"))
    client_instance.get = AsyncMock(return_value=good_response)

    await bot.message_handler(update_token, context)

    assert context.user_data["state"] == "NORMAL"
    assert context.bot.send_message.call_args.kwargs["text"] == bot.messages.get(
        "token_accepted", "en"
    )


# --- Message handler: load order & paths --------------------------------------


async def test_user_loaded_before_awaiting_reset_check(bot):
    update = _make_update(text="confirm")
    context = _make_context(user_data={"state": "AWAITING_RESET"})
    bot.db.get_user = AsyncMock(return_value=_make_user(bot, language="ru"))
    bot.db.clear_user_history = AsyncMock(return_value=2)

    await bot.message_handler(update, context)

    bot.db.get_user.assert_called_once()
    assert context.bot.send_message.call_args.kwargs["text"] == bot.messages.get(
        "reset_confirmed", "ru", count=2
    )


async def test_get_user_raises_sends_db_error_in_detected_language(bot):
    update = _make_update(text="hello there friend")
    context = _make_context()
    bot.db.get_user = AsyncMock(side_effect=RuntimeError("db down"))

    await bot.message_handler(update, context)

    bot.db.log.assert_called_once()
    assert bot.db.log.call_args.args[1] == "ERROR"
    assert context.bot.send_message.call_args.kwargs["text"] == bot.messages.get(
        "db_error", "en"
    )


# --- Language handling --------------------------------------------------------


async def test_language_changed_calls_update_language_not_touch_user(bot, monkeypatch):
    update = _make_update(text="Привет, как мои дела по задачам сегодня?")
    context = _make_context()
    bot.db.get_user = AsyncMock(return_value=_make_user(bot, language="en"))
    monkeypatch.setattr(bot.language, "resolve", lambda stored, text: ("ru", True))

    await bot.message_handler(update, context)

    bot.db.update_language.assert_called_once_with(1, "ru")
    bot.db.touch_user.assert_not_called()


async def test_language_unchanged_calls_touch_user_not_update_language(bot, monkeypatch):
    update = _make_update(text="what's due today?")
    context = _make_context()
    bot.db.get_user = AsyncMock(return_value=_make_user(bot, language="en"))
    monkeypatch.setattr(bot.language, "resolve", lambda stored, text: ("en", False))

    await bot.message_handler(update, context)

    bot.db.touch_user.assert_called_once_with(1)
    bot.db.update_language.assert_not_called()


# --- Message handler: other paths ---------------------------------------------


async def test_unregistered_user_sends_unregistered(bot):
    update = _make_update(text="hello there friend")
    context = _make_context()
    bot.db.get_user = AsyncMock(return_value=None)

    await bot.message_handler(update, context)

    assert context.bot.send_message.call_args.kwargs["text"] == bot.messages.get(
        "unregistered", "en"
    )


async def test_accidental_token_deletes_message_sends_token_accidental(bot):
    update = _make_update(text="a" * 40)
    context = _make_context()
    bot.db.get_user = AsyncMock(return_value=_make_user(bot))

    await bot.message_handler(update, context)

    update.message.delete.assert_called_once()
    assert context.bot.send_message.call_args.kwargs["text"] == bot.messages.get(
        "token_accidental", "en"
    )
    bot.llm.process_message.assert_not_called()


async def test_accidental_token_deletion_failure_still_sends_token_accidental(bot):
    update = _make_update(text="a" * 40)
    update.message.delete = AsyncMock(side_effect=RuntimeError("can't delete"))
    context = _make_context()
    bot.db.get_user = AsyncMock(return_value=_make_user(bot))

    await bot.message_handler(update, context)

    assert context.bot.send_message.call_args.kwargs["text"] == bot.messages.get(
        "token_accidental", "en"
    )


async def test_decrypt_token_raises_sends_decrypt_error(bot, monkeypatch):
    update = _make_update(text="what's due today?")
    context = _make_context()
    bot.db.get_user = AsyncMock(return_value=_make_user(bot))
    monkeypatch.setattr(
        bot.crypto, "decrypt_token", MagicMock(side_effect=ValueError("bad"))
    )

    await bot.message_handler(update, context)

    assert context.bot.send_message.call_args.kwargs["text"] == bot.messages.get(
        "decrypt_error", "en"
    )
    bot.llm.process_message.assert_not_called()


async def test_turn_start_is_timezone_aware(bot):
    captured = {}

    async def fake_process_message(user_id, text, token, lang, turn_start):
        captured["turn_start"] = turn_start
        return "answer"

    bot.llm.process_message = fake_process_message
    update = _make_update(text="what's due today?")
    context = _make_context()
    bot.db.get_user = AsyncMock(return_value=_make_user(bot))

    await bot.message_handler(update, context)

    assert captured["turn_start"].tzinfo is not None


# --- keep-typing --------------------------------------------------------------


async def test_keep_typing_sends_typing_action_until_cancelled(bot, monkeypatch):
    monkeypatch.setattr(bot, "_KEEP_TYPING_INTERVAL_SECONDS", 0)
    telegram_bot = AsyncMock()

    task = asyncio.ensure_future(bot._keep_typing(telegram_bot, 1))
    for _ in range(5):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    telegram_bot.send_chat_action.assert_called()


async def test_cancel_keep_typing_handles_none_task(bot):
    await bot._cancel_keep_typing(None)


# --- Token input handler -------------------------------------------------------


async def test_token_input_valid_deletes_message_saves_user_sends_accepted(
    bot, monkeypatch
):
    update = _make_update(text="a" * 40)
    context = _make_context(user_data={"state": "AWAITING_TOKEN"})
    _patch_token_validation(bot, monkeypatch)

    await bot.message_handler(update, context)

    update.message.delete.assert_called_once()
    bot.db.save_user.assert_called_once()
    assert context.bot.send_message.call_args.kwargs["text"] == bot.messages.get(
        "token_accepted", "en"
    )


async def test_token_input_non_200_sends_token_invalid_stays_awaiting(bot, monkeypatch):
    update = _make_update(text="a" * 40)
    context = _make_context(user_data={"state": "AWAITING_TOKEN"})
    _patch_token_validation(bot, monkeypatch, status_code=403)

    await bot.message_handler(update, context)

    assert context.user_data["state"] == "AWAITING_TOKEN"
    assert context.bot.send_message.call_args.kwargs["text"] == bot.messages.get(
        "token_invalid", "en"
    )
    bot.db.save_user.assert_not_called()


async def test_token_input_network_error_sends_token_network_error(bot, monkeypatch):
    update = _make_update(text="a" * 40)
    context = _make_context(user_data={"state": "AWAITING_TOKEN"})
    _patch_token_validation(
        bot, monkeypatch, get_side_effect=httpx.ConnectTimeout("timeout")
    )

    await bot.message_handler(update, context)

    assert context.user_data["state"] == "AWAITING_TOKEN"
    assert context.bot.send_message.call_args.kwargs["text"] == bot.messages.get(
        "token_network_error", "en"
    )


async def test_token_input_deletion_failure_continues(bot, monkeypatch):
    update = _make_update(text="a" * 40)
    update.message.delete = AsyncMock(side_effect=RuntimeError("can't delete"))
    context = _make_context(user_data={"state": "AWAITING_TOKEN"})
    _patch_token_validation(bot, monkeypatch)

    await bot.message_handler(update, context)

    sent_texts = [c.kwargs["text"] for c in context.bot.send_message.call_args_list]
    assert bot.messages.get("token_deletion_failed", "en") in sent_texts
    assert bot.messages.get("token_accepted", "en") in sent_texts
    bot.db.save_user.assert_called_once()


async def test_token_input_existing_user_evicts_old_token_cache(bot, monkeypatch):
    update = _make_update(text="b" * 40)
    context = _make_context(user_data={"state": "AWAITING_TOKEN"})
    bot.db.get_user = AsyncMock(return_value=_make_user(bot, plain_token="y" * 40))
    monkeypatch.setattr(bot.crypto, "decrypt_token", MagicMock(return_value="OLD_PLAIN"))
    monkeypatch.setattr(bot.crypto, "encrypt_token", MagicMock(return_value="new-encrypted"))
    _patch_token_validation(bot, monkeypatch)

    await bot.message_handler(update, context)

    bot.mcp.evict_cache.assert_called_once_with("OLD_PLAIN")


async def test_token_input_uses_detected_language_not_token_string(bot, monkeypatch):
    update = _make_update(text="c" * 40)
    context = _make_context(
        user_data={"state": "AWAITING_TOKEN", "detected_language": "ru"}
    )
    _patch_token_validation(bot, monkeypatch)

    await bot.message_handler(update, context)

    assert context.bot.send_message.call_args.kwargs["text"] == bot.messages.get(
        "token_accepted", "ru"
    )


async def test_token_input_detected_language_cleared_on_success(bot, monkeypatch):
    update = _make_update(text="d" * 40)
    context = _make_context(
        user_data={"state": "AWAITING_TOKEN", "detected_language": "ru"}
    )
    _patch_token_validation(bot, monkeypatch)

    await bot.message_handler(update, context)

    assert "detected_language" not in context.user_data


async def test_token_input_detected_language_preserved_on_invalid(bot, monkeypatch):
    update = _make_update(text="e" * 40)
    context = _make_context(
        user_data={"state": "AWAITING_TOKEN", "detected_language": "ru"}
    )
    _patch_token_validation(bot, monkeypatch, status_code=401)

    await bot.message_handler(update, context)

    assert context.user_data["detected_language"] == "ru"


# --- _send_with_truncation -----------------------------------------------------


async def test_send_with_truncation_under_limit_single_message(bot):
    telegram_bot = AsyncMock()
    await bot._send_with_truncation(telegram_bot, 1, "short text", "en")
    telegram_bot.send_message.assert_called_once_with(chat_id=1, text="short text")


async def test_send_with_truncation_splits_at_double_newline(bot):
    telegram_bot = AsyncMock()
    first_part = "a" * 4000
    second_part = "b" * 200
    text = first_part + "\n\n" + second_part
    await bot._send_with_truncation(telegram_bot, 1, text, "en")
    calls = telegram_bot.send_message.call_args_list
    assert len(calls) == 2
    assert calls[0].kwargs["text"] == first_part
    assert calls[1].kwargs["text"] == second_part


async def test_send_with_truncation_splits_at_single_newline_when_no_double(bot):
    telegram_bot = AsyncMock()
    first_part = "a" * 4000
    second_part = "b" * 200
    text = first_part + "\n" + second_part
    await bot._send_with_truncation(telegram_bot, 1, text, "en")
    calls = telegram_bot.send_message.call_args_list
    assert len(calls) == 2
    assert calls[0].kwargs["text"] == first_part
    assert calls[1].kwargs["text"] == second_part


async def test_send_with_truncation_hard_cuts_when_no_newline(bot):
    telegram_bot = AsyncMock()
    text = "a" * 4200
    await bot._send_with_truncation(telegram_bot, 1, text, "en")
    calls = telegram_bot.send_message.call_args_list
    assert len(calls) == 2
    assert calls[0].kwargs["text"] == "a" * 4096
    assert calls[1].kwargs["text"] == "a" * 104


async def test_send_with_truncation_failure_sends_send_error_directly(bot):
    telegram_bot = AsyncMock()
    telegram_bot.send_message = AsyncMock(
        side_effect=[RuntimeError("telegram down"), None]
    )
    await bot._send_with_truncation(telegram_bot, 1, "short text", "en")
    assert telegram_bot.send_message.call_count == 2
    second_call = telegram_bot.send_message.call_args_list[1]
    assert second_call.kwargs["text"] == bot.messages.get("send_error", "en")


async def test_send_with_truncation_both_fail_logged_to_stdout_only(bot, monkeypatch):
    telegram_bot = AsyncMock()
    telegram_bot.send_message = AsyncMock(side_effect=RuntimeError("telegram down"))
    log_stdout_mock = MagicMock()
    monkeypatch.setattr(bot.logger, "log_stdout", log_stdout_mock)

    await bot._send_with_truncation(telegram_bot, 1, "short text", "en")

    assert telegram_bot.send_message.call_count == 2
    log_stdout_mock.assert_called_once()


# --- Command handlers ----------------------------------------------------------


async def test_reset_command_loads_user_uses_stored_language(bot):
    update = _make_update(text="/reset")
    context = _make_context()
    bot.db.get_user = AsyncMock(return_value=_make_user(bot, language="ru"))

    await bot.reset_command(update, context)

    assert context.bot.send_message.call_args.kwargs["text"] == bot.messages.get(
        "reset_prompt", "ru"
    )


async def test_reset_command_unregistered_sends_unregistered_not_awaiting_reset(bot):
    update = _make_update(text="/reset")
    context = _make_context()
    bot.db.get_user = AsyncMock(return_value=None)

    await bot.reset_command(update, context)

    assert context.bot.send_message.call_args.kwargs["text"] == bot.messages.get(
        "unregistered", "en"
    )
    assert context.user_data.get("state") != "AWAITING_RESET"


async def test_refresh_command_unregistered_sends_unregistered(bot):
    update = _make_update(text="/refresh")
    context = _make_context()
    bot.db.get_user = AsyncMock(return_value=None)

    await bot.refresh_command(update, context)

    assert context.bot.send_message.call_args.kwargs["text"] == bot.messages.get(
        "unregistered", "en"
    )


async def test_refresh_command_registered_evicts_cache_sends_confirmed(bot, monkeypatch):
    update = _make_update(text="/refresh")
    context = _make_context()
    bot.db.get_user = AsyncMock(return_value=_make_user(bot, language="ru"))
    monkeypatch.setattr(bot.crypto, "decrypt_token", MagicMock(return_value="plain-token"))

    await bot.refresh_command(update, context)

    bot.mcp.evict_cache.assert_called_once_with("plain-token")
    assert context.bot.send_message.call_args.kwargs["text"] == bot.messages.get(
        "refresh_confirmed", "ru"
    )


async def test_help_command_registered_uses_stored_language(bot):
    update = _make_update(text="/help")
    context = _make_context()
    bot.db.get_user = AsyncMock(return_value=_make_user(bot, language="ru"))

    await bot.help_command(update, context)

    assert context.bot.send_message.call_args.kwargs["text"] == bot.messages.get(
        "help_text", "ru"
    )


async def test_help_command_unregistered_uses_detected_language(bot):
    update = _make_update(text="/help")
    context = _make_context()
    bot.db.get_user = AsyncMock(return_value=None)

    await bot.help_command(update, context)

    assert context.bot.send_message.call_args.kwargs["text"] == bot.messages.get(
        "help_text", "en"
    )


# --- Heartbeat task -------------------------------------------------------------


async def test_heartbeat_first_sleep_is_60(bot, monkeypatch):
    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 2:
            raise asyncio.CancelledError()

    monkeypatch.setattr(bot.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await bot._heartbeat(0.0)

    assert sleep_calls[0] == 60


async def test_heartbeat_logs_heartbeat_at_info(bot, monkeypatch):
    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 2:
            raise asyncio.CancelledError()

    monkeypatch.setattr(bot.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await bot._heartbeat(0.0)

    bot.db.log.assert_called_once()
    assert bot.db.log.call_args.args[1] == "INFO"
    assert bot.db.log.call_args.args[2] == "heartbeat"
    assert bot.db.log.call_args.args[3]["status"] == "alive"


async def test_heartbeat_continues_after_log_failure(bot, monkeypatch):
    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 3:
            raise asyncio.CancelledError()

    monkeypatch.setattr(bot.asyncio, "sleep", fake_sleep)
    bot.db.log = AsyncMock(side_effect=RuntimeError("log failed"))

    with pytest.raises(asyncio.CancelledError):
        await bot._heartbeat(0.0)

    assert bot.db.log.call_count == 2


# --- Daily cleanup task ----------------------------------------------------------


async def test_daily_cleanup_computes_sleep_from_utc(bot, monkeypatch):
    fixed_now = datetime(2026, 6, 23, 1, 0, tzinfo=timezone.utc)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(bot, "datetime", _FixedDatetime)

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        raise asyncio.CancelledError()

    monkeypatch.setattr(bot.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await bot._daily_cleanup()

    assert sleep_calls[0] == pytest.approx(2 * 3600, abs=1)


async def test_daily_cleanup_logs_info_on_success(bot, monkeypatch):
    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 2:
            raise asyncio.CancelledError()

    monkeypatch.setattr(bot.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await bot._daily_cleanup()

    bot.db.log.assert_called_once()
    assert bot.db.log.call_args.args[1] == "INFO"
    assert bot.db.log.call_args.args[2] == "daily_cleanup"


async def test_daily_cleanup_logs_error_and_continues_on_db_failure(bot, monkeypatch):
    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 2:
            raise asyncio.CancelledError()

    monkeypatch.setattr(bot.asyncio, "sleep", fake_sleep)
    bot.db.cleanup_old_conversations = AsyncMock(side_effect=RuntimeError("db down"))

    with pytest.raises(asyncio.CancelledError):
        await bot._daily_cleanup()

    bot.db.log.assert_called_once()
    assert bot.db.log.call_args.args[1] == "ERROR"
    assert bot.db.log.call_args.args[2] == "daily_cleanup"


# --- main() startup ---------------------------------------------------------------


async def test_post_init_failure_logs_and_raises(bot, monkeypatch):
    bot.db.init_pool = AsyncMock(side_effect=RuntimeError("connection refused"))
    log_stdout_mock = MagicMock()
    monkeypatch.setattr(bot.logger, "log_stdout", log_stdout_mock)
    application = MagicMock()
    application.bot_data = {"start_time": 0.0}

    with pytest.raises(RuntimeError):
        await bot._post_init(application)

    log_stdout_mock.assert_called_once()
    assert log_stdout_mock.call_args.args[0] == "ERROR"


async def test_post_init_success_starts_background_tasks(bot):
    application = MagicMock()
    application.bot_data = {"start_time": 0.0}

    await bot._post_init(application)

    assert "heartbeat_task" in application.bot_data
    assert "cleanup_task" in application.bot_data
    application.bot_data["heartbeat_task"].cancel()
    application.bot_data["cleanup_task"].cancel()


async def test_post_shutdown_cancels_tasks_and_closes_pool(bot):
    application = MagicMock()

    async def _never_ends():
        await asyncio.sleep(1000)

    heartbeat_task = asyncio.ensure_future(_never_ends())
    cleanup_task = asyncio.ensure_future(_never_ends())
    application.bot_data = {
        "heartbeat_task": heartbeat_task,
        "cleanup_task": cleanup_task,
    }

    await bot._post_shutdown(application)

    assert heartbeat_task.cancelled() or heartbeat_task.cancel()
    bot.db.close_pool.assert_called_once()
