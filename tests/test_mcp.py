import importlib
import json
import sys
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
    "MCP_TOOLS_TTL": "86400",
}


@pytest.fixture
def mcp(monkeypatch):
    for key, value in REQUIRED_VARS.items():
        monkeypatch.setenv(key, value)
    sys.modules.pop("config", None)
    sys.modules.pop("mcp", None)
    module = importlib.import_module("mcp")
    module._cache.clear()
    return module


def _sse_response(payload: dict, status_code: int = 200) -> httpx.Response:
    body = f"data: {json.dumps(payload)}\n\n"
    return httpx.Response(
        status_code, text=body, request=httpx.Request("POST", "https://example.com")
    )


def _patch_async_client(mcp_module, monkeypatch, response=None, side_effect=None):
    client_instance = AsyncMock()
    if side_effect is not None:
        client_instance.post = AsyncMock(side_effect=side_effect)
    else:
        client_instance.post = AsyncMock(return_value=response)
    client_instance.__aenter__ = AsyncMock(return_value=client_instance)
    client_instance.__aexit__ = AsyncMock(return_value=False)
    client_cls = MagicMock(return_value=client_instance)
    monkeypatch.setattr(mcp_module.httpx, "AsyncClient", client_cls)
    return client_cls, client_instance


RAW_TOOL = {
    "name": "find-tasks",
    "description": "Filter tasks",
    "inputSchema": {
        "type": "object",
        "properties": {"project": {"type": "string"}},
    },
}

CONVERTED_TOOL = {
    "type": "function",
    "function": {
        "name": "find-tasks",
        "description": "Filter tasks",
        "parameters": {
            "type": "object",
            "properties": {"project": {"type": "string"}},
        },
    },
}


# --- SSE parser --------------------------------------------------------


def test_parse_sse_returns_parsed_json(mcp):
    text = 'data: {"jsonrpc": "2.0", "id": 1, "result": {"ok": true}}\n\n'
    assert mcp._parse_sse(text) == {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}


def test_parse_sse_raises_descriptive_error_with_raw_text_when_no_data_line(mcp):
    text = "event: message\nid: 1\n\n"
    with pytest.raises(ValueError) as exc_info:
        mcp._parse_sse(text)
    assert text in str(exc_info.value)


def test_parse_sse_returns_only_data_line_content(mcp):
    text = 'event: message\nid: 42\ndata: {"result": "value"}\n\n'
    assert mcp._parse_sse(text) == {"result": "value"}


def test_parse_sse_strips_leading_trailing_whitespace(mcp):
    text = '  data:   {"result": "value"}   \n\n'
    assert mcp._parse_sse(text) == {"result": "value"}


# --- Schema converter -----------------------------------------------------


def test_convert_tool_renames_input_schema_to_parameters(mcp):
    converted = mcp._convert_tool(RAW_TOOL)
    assert "parameters" in converted["function"]
    assert "inputSchema" not in converted["function"]


def test_convert_tool_wraps_in_function_type(mcp):
    converted = mcp._convert_tool(RAW_TOOL)
    assert converted == CONVERTED_TOOL


def test_convert_tool_passes_name_and_description_unchanged(mcp):
    converted = mcp._convert_tool(RAW_TOOL)
    assert converted["function"]["name"] == "find-tasks"
    assert converted["function"]["description"] == "Filter tasks"


def test_convert_tool_passes_nested_schema_properties_unchanged(mcp):
    converted = mcp._convert_tool(RAW_TOOL)
    assert converted["function"]["parameters"]["properties"] == {
        "project": {"type": "string"}
    }


def test_convert_tool_empty_input_schema_produces_empty_parameters(mcp):
    tool = {"name": "noop", "description": "Does nothing", "inputSchema": {}}
    converted = mcp._convert_tool(tool)
    assert converted["function"]["parameters"] == {}


# --- Tool cache -------------------------------------------------------------


async def test_get_tools_fetches_on_first_call(mcp, monkeypatch):
    response = _sse_response(
        {"jsonrpc": "2.0", "id": 1, "result": {"tools": [RAW_TOOL]}}
    )
    client_cls, client_instance = _patch_async_client(mcp, monkeypatch, response=response)
    tools = await mcp.get_tools("token-a")
    assert tools == [CONVERTED_TOOL]
    client_instance.post.assert_called_once()


async def test_get_tools_returns_cached_on_second_call(mcp, monkeypatch):
    response = _sse_response(
        {"jsonrpc": "2.0", "id": 1, "result": {"tools": [RAW_TOOL]}}
    )
    client_cls, client_instance = _patch_async_client(mcp, monkeypatch, response=response)
    await mcp.get_tools("token-a")
    tools = await mcp.get_tools("token-a")
    assert tools == [CONVERTED_TOOL]
    client_instance.post.assert_called_once()


async def test_get_tools_cache_expires_after_ttl(mcp, monkeypatch):
    response = _sse_response(
        {"jsonrpc": "2.0", "id": 1, "result": {"tools": [RAW_TOOL]}}
    )
    client_cls, client_instance = _patch_async_client(mcp, monkeypatch, response=response)
    fake_time = [1_000_000.0]
    monkeypatch.setattr(mcp.time, "time", lambda: fake_time[0])
    await mcp.get_tools("token-a")
    fake_time[0] += mcp.config.MCP_TOOLS_TTL + 1
    await mcp.get_tools("token-a")
    assert client_instance.post.call_count == 2


async def test_get_tools_after_ttl_expiry_returns_openai_format_not_raw(
    mcp, monkeypatch
):
    response = _sse_response(
        {"jsonrpc": "2.0", "id": 1, "result": {"tools": [RAW_TOOL]}}
    )
    client_cls, client_instance = _patch_async_client(mcp, monkeypatch, response=response)
    fake_time = [1_000_000.0]
    monkeypatch.setattr(mcp.time, "time", lambda: fake_time[0])
    await mcp.get_tools("token-a")
    fake_time[0] += mcp.config.MCP_TOOLS_TTL + 1
    tools = await mcp.get_tools("token-a")
    assert tools == [CONVERTED_TOOL]
    for tool in tools:
        assert tool["type"] == "function"
        assert "name" not in tool
        assert "inputSchema" not in tool["function"]


async def test_evict_cache_causes_next_get_tools_to_fetch_again(mcp, monkeypatch):
    response = _sse_response(
        {"jsonrpc": "2.0", "id": 1, "result": {"tools": [RAW_TOOL]}}
    )
    client_cls, client_instance = _patch_async_client(mcp, monkeypatch, response=response)
    await mcp.get_tools("token-a")
    mcp.evict_cache("token-a")
    await mcp.get_tools("token-a")
    assert client_instance.post.call_count == 2


def test_evict_cache_is_silent_when_token_absent(mcp):
    mcp.evict_cache("never-cached-token")


async def test_different_tokens_have_independent_cache_entries(mcp, monkeypatch):
    response = _sse_response(
        {"jsonrpc": "2.0", "id": 1, "result": {"tools": [RAW_TOOL]}}
    )
    client_cls, client_instance = _patch_async_client(mcp, monkeypatch, response=response)
    await mcp.get_tools("token-a")
    await mcp.get_tools("token-b")
    assert client_instance.post.call_count == 2
    await mcp.get_tools("token-a")
    await mcp.get_tools("token-b")
    assert client_instance.post.call_count == 2


async def test_call_tool_raises_on_json_rpc_error_field(mcp, monkeypatch):
    response = _sse_response(
        {"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "bad params"}}
    )
    _patch_async_client(mcp, monkeypatch, response=response)
    with pytest.raises(RuntimeError):
        await mcp.call_tool("token-a", "find-tasks", {})


# --- call_tool ---------------------------------------------------------------


async def test_call_tool_returns_result_field(mcp, monkeypatch):
    response = _sse_response(
        {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "ok"}]}}
    )
    _patch_async_client(mcp, monkeypatch, response=response)
    result = await mcp.call_tool("token-a", "find-tasks", {"project": "Inbox"})
    assert result == {"content": [{"type": "text", "text": "ok"}]}


async def test_call_tool_raises_on_http_error_status(mcp, monkeypatch):
    response = _sse_response({"error": "boom"}, status_code=500)
    _patch_async_client(mcp, monkeypatch, response=response)
    with pytest.raises(httpx.HTTPStatusError):
        await mcp.call_tool("token-a", "find-tasks", {})


async def test_call_tool_raises_on_sse_parse_failure(mcp, monkeypatch):
    response = httpx.Response(
        200, text="event: message\n\n", request=httpx.Request("POST", "https://x")
    )
    _patch_async_client(mcp, monkeypatch, response=response)
    with pytest.raises(ValueError):
        await mcp.call_tool("token-a", "find-tasks", {})


async def test_call_tool_raises_on_timeout(mcp, monkeypatch):
    _patch_async_client(
        mcp, monkeypatch, side_effect=httpx.TimeoutException("timed out")
    )
    with pytest.raises(httpx.TimeoutException):
        await mcp.call_tool("token-a", "find-tasks", {})


async def test_call_tool_uses_total_timeout_of_ten_seconds(mcp, monkeypatch):
    response = _sse_response({"jsonrpc": "2.0", "id": 1, "result": {}})
    client_cls, client_instance = _patch_async_client(mcp, monkeypatch, response=response)
    await mcp.call_tool("token-a", "find-tasks", {})
    assert client_cls.call_args.kwargs["timeout"] == httpx.Timeout(10.0)


async def test_call_tool_sends_bearer_and_sse_accept_headers(mcp, monkeypatch):
    response = _sse_response({"jsonrpc": "2.0", "id": 1, "result": {}})
    client_cls, client_instance = _patch_async_client(mcp, monkeypatch, response=response)
    await mcp.call_tool("secret-token", "find-tasks", {})
    _, post_kwargs = client_instance.post.call_args
    headers = post_kwargs["headers"]
    assert headers["Authorization"] == "Bearer secret-token"
    assert headers["Accept"] == "application/json, text/event-stream"
