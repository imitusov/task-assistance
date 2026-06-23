import importlib
import json
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
    "SECRET_KEY": "test-secret-key",
    "LOG_LEVEL": "INFO",
}

TURN_START = datetime(2026, 6, 23, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def llm(monkeypatch):
    for key, value in REQUIRED_VARS.items():
        monkeypatch.setenv(key, value)
    for name in ("config", "messages", "db", "mcp", "llm"):
        sys.modules.pop(name, None)
    module = importlib.import_module("llm")

    module.db = MagicMock()
    module.db.get_history = AsyncMock(return_value=[])
    module.db.save_user_message = AsyncMock()
    module.db.save_turn = AsyncMock()
    module.db.save_tool_result = AsyncMock()
    module.db.delete_turn_tool_results = AsyncMock()
    module.db.log = AsyncMock()

    module.mcp = MagicMock()
    module.mcp.get_tools = AsyncMock(return_value=[])
    module.mcp.call_tool = AsyncMock(return_value={"ok": True})

    return module


def _llm_response(payload: dict, status_code: int = 200, headers: dict | None = None):
    return httpx.Response(
        status_code,
        json=payload,
        headers=headers or {},
        request=httpx.Request("POST", "https://api.neuraldeep.ru/v1/chat/completions"),
    )


def _message_payload(content=None, tool_calls=None, usage=None):
    message = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    payload = {"choices": [{"message": message}]}
    if usage is not None:
        payload["usage"] = usage
    return payload


def _tool_call(call_id, name, arguments):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def _patch_llm_client(llm_module, monkeypatch, responses):
    client_instance = AsyncMock()
    client_instance.post = AsyncMock(side_effect=responses)
    client_instance.__aenter__ = AsyncMock(return_value=client_instance)
    client_instance.__aexit__ = AsyncMock(return_value=False)
    client_cls = MagicMock(return_value=client_instance)
    monkeypatch.setattr(llm_module.httpx, "AsyncClient", client_cls)
    return client_cls, client_instance


# --- Direct answer (no tools) -----------------------------------------------


async def test_direct_answer_saves_user_message_before_llm_call(llm, monkeypatch):
    call_order = []
    llm.db.save_user_message.side_effect = lambda *a, **k: call_order.append(
        "save_user_message"
    )
    response = _llm_response(_message_payload(content="Here are your tasks."))
    client_cls, client_instance = _patch_llm_client(llm, monkeypatch, [response])
    client_instance.post.side_effect = lambda *a, **k: (
        call_order.append("llm_call") or response
    )

    await llm.process_message(1, "what's due today?", "tok", "en", TURN_START)

    assert call_order == ["save_user_message", "llm_call"]


async def test_direct_answer_calls_save_turn_once_no_tool_result(llm, monkeypatch):
    response = _llm_response(_message_payload(content="Here are your tasks."))
    _patch_llm_client(llm, monkeypatch, [response])

    await llm.process_message(1, "what's due today?", "tok", "en", TURN_START)

    llm.db.save_turn.assert_called_once()
    llm.db.save_tool_result.assert_not_called()


async def test_direct_answer_returns_llm_content(llm, monkeypatch):
    response = _llm_response(_message_payload(content="Here are your tasks."))
    _patch_llm_client(llm, monkeypatch, [response])

    answer = await llm.process_message(1, "what's due today?", "tok", "en", TURN_START)

    assert answer == "Here are your tasks."


# --- Tool calling path ---------------------------------------------------


async def test_one_tool_call_calls_call_tool_once_and_makes_second_llm_call(
    llm, monkeypatch
):
    tool_call = _tool_call("call-1", "find-tasks", {"project": "Inbox"})
    first = _llm_response(_message_payload(content=None, tool_calls=[tool_call]))
    second = _llm_response(_message_payload(content="You have 2 tasks."))
    _patch_llm_client(llm, monkeypatch, [first, second])

    answer = await llm.process_message(1, "what's due today?", "tok", "en", TURN_START)

    llm.mcp.call_tool.assert_awaited_once_with("tok", "find-tasks", {"project": "Inbox"})
    assert answer == "You have 2 tasks."
    llm.db.save_turn.assert_called_once()


async def test_two_tool_calls_both_executed_and_results_appended_before_second_call(
    llm, monkeypatch
):
    tool_call_1 = _tool_call("call-1", "find-tasks", {"project": "Inbox"})
    tool_call_2 = _tool_call("call-2", "find-projects", {})
    first = _llm_response(
        _message_payload(content=None, tool_calls=[tool_call_1, tool_call_2])
    )
    second = _llm_response(_message_payload(content="Done."))
    client_cls, client_instance = _patch_llm_client(llm, monkeypatch, [first, second])

    llm.mcp.call_tool = AsyncMock(
        side_effect=[{"result": "tasks"}, {"result": "projects"}]
    )

    await llm.process_message(1, "show me everything", "tok", "en", TURN_START)

    assert llm.mcp.call_tool.await_count == 2
    second_call_messages = client_instance.post.call_args_list[1].kwargs["json"][
        "messages"
    ]
    tool_role_messages = [m for m in second_call_messages if m.get("role") == "tool"]
    assert len(tool_role_messages) == 2
    assert json.loads(tool_role_messages[0]["content"]) == {"result": "tasks"}
    assert json.loads(tool_role_messages[1]["content"]) == {"result": "projects"}


async def test_save_tool_result_called_once_per_tool_call_save_turn_once(
    llm, monkeypatch
):
    tool_call_1 = _tool_call("call-1", "find-tasks", {})
    tool_call_2 = _tool_call("call-2", "find-projects", {})
    first = _llm_response(
        _message_payload(content=None, tool_calls=[tool_call_1, tool_call_2])
    )
    second = _llm_response(_message_payload(content="Done."))
    _patch_llm_client(llm, monkeypatch, [first, second])

    await llm.process_message(1, "show me everything", "tok", "en", TURN_START)

    assert llm.db.save_tool_result.await_count == 2
    llm.db.save_turn.assert_called_once()


# --- turn_start propagation -----------------------------------------------


async def test_turn_start_propagation_on_tool_failure(llm, monkeypatch):
    tool_call = _tool_call("call-1", "find-tasks", {})
    first = _llm_response(_message_payload(content=None, tool_calls=[tool_call]))
    _patch_llm_client(llm, monkeypatch, [first])
    llm.mcp.call_tool = AsyncMock(side_effect=RuntimeError("mcp down"))

    await llm.process_message(1, "do a thing", "tok", "en", TURN_START)

    llm.db.delete_turn_tool_results.assert_awaited_once()
    args = llm.db.delete_turn_tool_results.call_args.args
    assert args[1] is TURN_START


async def test_turn_start_propagation_on_rate_limit_after_tool_calls(llm, monkeypatch):
    tool_call = _tool_call("call-1", "find-tasks", {})
    first = _llm_response(_message_payload(content=None, tool_calls=[tool_call]))
    second = _llm_response(
        {}, status_code=429, headers={"X-Window": "session", "Retry-After": "60"}
    )
    _patch_llm_client(llm, monkeypatch, [first, second])

    await llm.process_message(1, "do a thing", "tok", "en", TURN_START)

    llm.db.delete_turn_tool_results.assert_awaited_once()
    args = llm.db.delete_turn_tool_results.call_args.args
    assert args[1] is TURN_START


async def test_turn_start_propagation_on_llm_timeout_after_tool_calls(llm, monkeypatch):
    tool_call = _tool_call("call-1", "find-tasks", {})
    first = _llm_response(_message_payload(content=None, tool_calls=[tool_call]))
    _patch_llm_client(
        llm, monkeypatch, [first, httpx.TimeoutException("timed out")]
    )

    await llm.process_message(1, "do a thing", "tok", "en", TURN_START)

    llm.db.delete_turn_tool_results.assert_awaited_once()
    args = llm.db.delete_turn_tool_results.call_args.args
    assert args[1] is TURN_START


async def test_turn_start_propagation_on_save_tool_result_failure(llm, monkeypatch):
    tool_call = _tool_call("call-1", "find-tasks", {})
    first = _llm_response(_message_payload(content=None, tool_calls=[tool_call]))
    _patch_llm_client(llm, monkeypatch, [first])
    llm.db.save_tool_result = AsyncMock(side_effect=RuntimeError("db down"))

    await llm.process_message(1, "do a thing", "tok", "en", TURN_START)

    llm.db.delete_turn_tool_results.assert_awaited_once()
    args = llm.db.delete_turn_tool_results.call_args.args
    assert args[1] is TURN_START


# --- Early return paths ----------------------------------------------------


async def test_http_429_first_call_returns_rate_limit_no_save_turn(llm, monkeypatch):
    response = _llm_response(
        {}, status_code=429, headers={"X-Window": "session", "Retry-After": "60"}
    )
    _patch_llm_client(llm, monkeypatch, [response])

    answer = await llm.process_message(1, "hi", "tok", "en", TURN_START)

    assert "Session limit" in answer or "Weekly limit" in answer
    llm.db.save_turn.assert_not_called()
    llm.db.save_user_message.assert_called_once()
    llm.db.delete_turn_tool_results.assert_not_called()


async def test_http_429_second_call_calls_delete_turn_tool_results(llm, monkeypatch):
    tool_call = _tool_call("call-1", "find-tasks", {})
    first = _llm_response(_message_payload(content=None, tool_calls=[tool_call]))
    second = _llm_response(
        {}, status_code=429, headers={"X-Window": "week", "Retry-After": "100"}
    )
    _patch_llm_client(llm, monkeypatch, [first, second])

    answer = await llm.process_message(1, "hi", "tok", "en", TURN_START)

    llm.db.delete_turn_tool_results.assert_called_once()
    assert "Weekly limit" in answer


async def test_llm_timeout_first_call_returns_llm_timeout(llm, monkeypatch):
    _patch_llm_client(llm, monkeypatch, [httpx.TimeoutException("timed out")])

    answer = await llm.process_message(1, "hi", "tok", "en", TURN_START)

    assert answer == llm.messages.get("llm_timeout", "en")


async def test_llm_timeout_second_call_calls_delete_turn_tool_results(llm, monkeypatch):
    tool_call = _tool_call("call-1", "find-tasks", {})
    first = _llm_response(_message_payload(content=None, tool_calls=[tool_call]))
    _patch_llm_client(llm, monkeypatch, [first, httpx.TimeoutException("timed out")])

    answer = await llm.process_message(1, "hi", "tok", "en", TURN_START)

    llm.db.delete_turn_tool_results.assert_called_once()
    assert answer == llm.messages.get("llm_timeout", "en")


async def test_tool_failure_skips_remaining_tool_calls_returns_tool_failure(
    llm, monkeypatch
):
    tool_call_1 = _tool_call("call-1", "find-tasks", {})
    tool_call_2 = _tool_call("call-2", "find-projects", {})
    first = _llm_response(
        _message_payload(content=None, tool_calls=[tool_call_1, tool_call_2])
    )
    _patch_llm_client(llm, monkeypatch, [first])
    llm.mcp.call_tool = AsyncMock(side_effect=RuntimeError("mcp down"))

    answer = await llm.process_message(1, "hi", "tok", "en", TURN_START)

    assert llm.mcp.call_tool.await_count == 1
    llm.db.delete_turn_tool_results.assert_called_once()
    assert answer == llm.messages.get("tool_failure", "en")


async def test_save_tool_result_failure_returns_tool_failure(llm, monkeypatch):
    tool_call = _tool_call("call-1", "find-tasks", {})
    first = _llm_response(_message_payload(content=None, tool_calls=[tool_call]))
    _patch_llm_client(llm, monkeypatch, [first])
    llm.db.save_tool_result = AsyncMock(side_effect=RuntimeError("db down"))

    answer = await llm.process_message(1, "hi", "tok", "en", TURN_START)

    llm.db.delete_turn_tool_results.assert_called_once()
    assert answer == llm.messages.get("tool_failure", "en")


# --- Rate limit --------------------------------------------------------------


async def test_rate_limit_session_window_returns_session_message(llm, monkeypatch):
    response = _llm_response(
        {}, status_code=429, headers={"X-Window": "session", "Retry-After": "60"}
    )
    _patch_llm_client(llm, monkeypatch, [response])

    answer = await llm.process_message(1, "hi", "tok", "en", TURN_START)

    assert answer == llm.messages.get("rate_limit_session", "en", retry_time="1m")


async def test_rate_limit_week_window_returns_week_message(llm, monkeypatch):
    response = _llm_response(
        {}, status_code=429, headers={"X-Window": "week", "Retry-After": "100"}
    )
    _patch_llm_client(llm, monkeypatch, [response])

    answer = await llm.process_message(1, "hi", "tok", "en", TURN_START)

    assert "Weekly limit" in answer


async def test_rate_limit_logged_at_warning_not_error(llm, monkeypatch):
    response = _llm_response(
        {}, status_code=429, headers={"X-Window": "session", "Retry-After": "60"}
    )
    _patch_llm_client(llm, monkeypatch, [response])

    await llm.process_message(1, "hi", "tok", "en", TURN_START)

    rate_limit_calls = [
        c for c in llm.db.log.call_args_list if c.args[2] == "rate_limit"
    ]
    assert len(rate_limit_calls) == 1
    assert rate_limit_calls[0].args[1] == "WARNING"
    error_calls = [c for c in llm.db.log.call_args_list if c.args[1] == "ERROR"]
    assert error_calls == []


async def test_rate_limit_retry_after_value_appears_in_message(llm, monkeypatch):
    response = _llm_response(
        {}, status_code=429, headers={"X-Window": "session", "Retry-After": "3600"}
    )
    _patch_llm_client(llm, monkeypatch, [response])

    await llm.process_message(1, "hi", "tok", "en", TURN_START)

    rate_limit_call = next(
        c for c in llm.db.log.call_args_list if c.args[2] == "rate_limit"
    )
    data = rate_limit_call.args[3]
    assert data["retry_after_seconds"] == 3600
    assert data["retry_after_human"] in llm.messages.get(
        "rate_limit_session", "en", retry_time=data["retry_after_human"]
    )


# --- Thinking tokens ----------------------------------------------------------


async def test_thinking_block_stripped_returns_answer_only(llm, monkeypatch):
    response = _llm_response(
        _message_payload(content="<think>reasoning here</think>The answer is 42.")
    )
    _patch_llm_client(llm, monkeypatch, [response])

    answer = await llm.process_message(1, "hi", "tok", "en", TURN_START)

    assert answer == "The answer is 42."


async def test_no_thinking_block_response_unchanged(llm, monkeypatch):
    response = _llm_response(_message_payload(content="The answer is 42."))
    _patch_llm_client(llm, monkeypatch, [response])

    answer = await llm.process_message(1, "hi", "tok", "en", TURN_START)

    assert answer == "The answer is 42."


def test_strip_thinking_handles_nested_blocks(llm):
    text = "<think>outer<think>inner</think>more</think>final answer"
    assert llm._strip_thinking(text) == "final answer"


def test_strip_thinking_handles_incomplete_block(llm):
    text = "before<think>never closes"
    assert llm._strip_thinking(text) == "before"


def test_strip_thinking_passes_through_none(llm):
    assert llm._strip_thinking(None) is None


def test_format_retry_time_includes_days_and_hours(llm):
    assert llm._format_retry_time(90000) == "1d 1h"


def test_format_retry_time_falls_back_to_seconds(llm):
    assert llm._format_retry_time(45) == "45s"


# --- token_count ---------------------------------------------------------------


async def test_token_count_logged_when_usage_present(llm, monkeypatch):
    response = _llm_response(
        _message_payload(content="answer", usage={"total_tokens": 123})
    )
    _patch_llm_client(llm, monkeypatch, [response])

    await llm.process_message(1, "hi", "tok", "en", TURN_START)

    llm_call_logs = [c for c in llm.db.log.call_args_list if c.args[2] == "llm_call"]
    assert llm_call_logs[0].args[3]["token_count"] == 123


async def test_token_count_logged_as_none_when_usage_absent(llm, monkeypatch):
    response = _llm_response(_message_payload(content="answer"))
    _patch_llm_client(llm, monkeypatch, [response])

    await llm.process_message(1, "hi", "tok", "en", TURN_START)

    llm_call_logs = [c for c in llm.db.log.call_args_list if c.args[2] == "llm_call"]
    assert llm_call_logs[0].args[3]["token_count"] is None


# --- System prompt -------------------------------------------------------------


def test_system_prompt_en(llm, monkeypatch):
    monkeypatch.setattr(llm, "_today_str", lambda: "2026-06-23")
    message = llm._build_system_message("en")
    assert message["role"] == "system"
    assert "2026-06-23" in message["content"]


def test_system_prompt_ru(llm, monkeypatch):
    monkeypatch.setattr(llm, "_today_str", lambda: "2026-06-23")
    message = llm._build_system_message("ru")
    assert "2026-06-23" in message["content"]
    assert message["content"] != llm._build_system_message("en")["content"]


async def test_system_prompt_date_is_injected_into_first_llm_call(llm, monkeypatch):
    monkeypatch.setattr(llm, "_today_str", lambda: "2099-01-01")
    response = _llm_response(_message_payload(content="answer"))
    client_cls, client_instance = _patch_llm_client(llm, monkeypatch, [response])

    await llm.process_message(1, "hi", "tok", "en", TURN_START)

    sent_messages = client_instance.post.call_args.kwargs["json"]["messages"]
    system_message = sent_messages[0]
    assert system_message["role"] == "system"
    assert "2099-01-01" in system_message["content"]
