import json
import time

import httpx

import config

MCP_ENDPOINT = "https://ai.todoist.net/mcp"

_TIMEOUT = httpx.Timeout(10.0)

_cache: dict[str, tuple[list, float]] = {}


def _parse_sse(text: str) -> dict:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("data:"):
            payload = line[len("data:") :].strip()
            return json.loads(payload)
    raise ValueError(f"No SSE 'data:' line found in MCP response: {text}")


def _convert_tool(tool: dict) -> dict:
    function = {key: value for key, value in tool.items() if key != "inputSchema"}
    function["parameters"] = tool.get("inputSchema", {})
    return {"type": "function", "function": function}


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }


async def _call_rpc(token: str, method: str, params: dict | None = None) -> dict:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.post(
            MCP_ENDPOINT, json=payload, headers=_headers(token)
        )
    response.raise_for_status()
    parsed = _parse_sse(response.text)
    if "error" in parsed:
        raise RuntimeError(f"MCP server returned an error: {parsed['error']}")
    return parsed["result"]


async def get_tools(token: str) -> list:
    cached = _cache.get(token)
    if cached is not None:
        tools, fetched_at = cached
        if time.time() - fetched_at < config.MCP_TOOLS_TTL:
            return tools

    result = await _call_rpc(token, "tools/list")
    converted = [_convert_tool(tool) for tool in result.get("tools", [])]
    _cache[token] = (converted, time.time())
    return converted


async def call_tool(token: str, name: str, arguments: dict) -> dict:
    return await _call_rpc(token, "tools/call", {"name": name, "arguments": arguments})


def evict_cache(token: str) -> None:
    _cache.pop(token, None)
