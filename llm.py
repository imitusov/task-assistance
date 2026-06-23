import json
import time
from datetime import datetime, timezone

import httpx

import config
import db
import mcp
import messages

_MODEL = config.LLM_MODEL
_LLM_TIMEOUT = httpx.Timeout(30.0)

_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"

_SYSTEM_PROMPTS = {
    "en": (
        "You are a helpful Todoist assistant. Today's date is {current_date} "
        "(UTC). Help the user manage and answer questions about their Todoist "
        "tasks using the available tools. Be concise and direct."
    ),
    "ru": (
        "Ты — полезный ассистент Todoist. Сегодняшняя дата: {current_date} "
        "(UTC). Помогай пользователю управлять задачами Todoist и отвечать на "
        "вопросы о них, используя доступные инструменты. Отвечай кратко и по "
        "делу."
    ),
}


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _build_system_message(language: str) -> dict:
    template = _SYSTEM_PROMPTS.get(language, _SYSTEM_PROMPTS["en"])
    return {"role": "system", "content": template.format(current_date=_today_str())}


def _strip_thinking(text: str | None) -> str | None:
    if text is None:
        return text
    result = []
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        if text.startswith(_THINK_OPEN, i):
            depth += 1
            i += len(_THINK_OPEN)
        elif text.startswith(_THINK_CLOSE, i):
            if depth > 0:
                depth -= 1
            i += len(_THINK_CLOSE)
        else:
            if depth == 0:
                result.append(text[i])
            i += 1
    return "".join(result).strip()


def _format_retry_time(seconds: int) -> str:
    seconds = max(seconds, 0)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


def _extract_token_count(payload: dict) -> int | None:
    usage = payload.get("usage")
    if not usage:
        return None
    return usage.get("total_tokens")


async def _call_llm(messages_payload: list[dict], tools: list):
    payload = {
        "model": _MODEL,
        "messages": messages_payload,
        "tools": tools,
        "tool_choice": "auto",
    }
    headers = {
        "Authorization": f"Bearer {config.NEURALDEEP_API_KEY}",
        "Content-Type": "application/json",
    }
    url = f"{config.NEURALDEEP_API_URL}/chat/completions"
    start = time.monotonic()
    async with httpx.AsyncClient(timeout=_LLM_TIMEOUT) as client:
        response = await client.post(url, json=payload, headers=headers)
    latency_ms = (time.monotonic() - start) * 1000
    return response, latency_ms


async def _handle_rate_limit(user_id: int, response: httpx.Response, language: str) -> str:
    window = response.headers.get("X-Window")
    retry_after = int(response.headers.get("Retry-After", "0"))
    retry_human = _format_retry_time(retry_after)
    await db.log(
        user_id,
        "WARNING",
        "rate_limit",
        {
            "window": window,
            "retry_after_seconds": retry_after,
            "retry_after_human": retry_human,
        },
    )
    key = "rate_limit_week" if window == "week" else "rate_limit_session"
    return messages.get(key, language, retry_time=retry_human)


async def process_message(
    user_id: int,
    user_text: str,
    token: str,
    language: str,
    turn_start: datetime,
) -> str:
    turn_started_at = time.monotonic()

    history = await db.get_history(user_id)
    system_message = _build_system_message(language)
    user_msg = {"role": "user", "content": user_text}
    await db.save_user_message(user_id, user_msg)
    conversation = list(history) + [user_msg]
    tools = await mcp.get_tools(token)

    try:
        response, latency_ms = await _call_llm([system_message] + conversation, tools)
    except httpx.TimeoutException:
        await db.log(
            user_id,
            "WARNING",
            "llm_call",
            {"stage": "tool_selection", "error": "timeout"},
        )
        return messages.get("llm_timeout", language)

    if response.status_code == 429:
        return await _handle_rate_limit(user_id, response, language)

    response.raise_for_status()
    payload = response.json()
    message = payload["choices"][0]["message"]
    await db.log(
        user_id,
        "INFO",
        "llm_call",
        {
            "stage": "tool_selection",
            "latency_ms": latency_ms,
            "token_count": _extract_token_count(payload),
        },
    )

    content = message.get("content")
    tool_calls = message.get("tool_calls")

    if not tool_calls:
        answer = _strip_thinking(content)
        await db.save_turn(user_id, {"role": "assistant", "content": answer})
        await db.log(
            user_id,
            "INFO",
            "bot_response",
            {"latency_ms": (time.monotonic() - turn_started_at) * 1000},
        )
        return answer

    assistant_tool_msg = {
        "role": "assistant",
        "content": content,
        "tool_calls": tool_calls,
    }
    conversation.append(assistant_tool_msg)

    for tool_call in tool_calls:
        name = tool_call["function"]["name"]
        arguments = json.loads(tool_call["function"]["arguments"])
        call_start = time.monotonic()

        try:
            result = await mcp.call_tool(token, name, arguments)
        except Exception as exc:
            await db.log(user_id, "ERROR", "tool_call", {"tool": name, "error": str(exc)})
            await db.delete_turn_tool_results(user_id, turn_start)
            return messages.get("tool_failure", language)

        tool_latency_ms = (time.monotonic() - call_start) * 1000
        tool_msg = {
            "role": "tool",
            "tool_call_id": tool_call["id"],
            "content": json.dumps(result),
        }

        try:
            await db.save_tool_result(user_id, tool_msg)
        except Exception as exc:
            await db.log(user_id, "ERROR", "tool_call", {"tool": name, "error": str(exc)})
            await db.delete_turn_tool_results(user_id, turn_start)
            return messages.get("tool_failure", language)

        conversation.append(tool_msg)
        await db.log(
            user_id,
            "INFO",
            "tool_call",
            {"tool": name, "params": arguments, "latency_ms": tool_latency_ms},
        )

    try:
        response2, latency_ms2 = await _call_llm([system_message] + conversation, tools)
    except httpx.TimeoutException:
        await db.delete_turn_tool_results(user_id, turn_start)
        await db.log(
            user_id,
            "WARNING",
            "llm_call",
            {"stage": "answer_generation", "error": "timeout"},
        )
        return messages.get("llm_timeout", language)

    if response2.status_code == 429:
        await db.delete_turn_tool_results(user_id, turn_start)
        return await _handle_rate_limit(user_id, response2, language)

    response2.raise_for_status()
    payload2 = response2.json()
    message2 = payload2["choices"][0]["message"]
    await db.log(
        user_id,
        "INFO",
        "llm_call",
        {
            "stage": "answer_generation",
            "latency_ms": latency_ms2,
            "token_count": _extract_token_count(payload2),
        },
    )

    answer = _strip_thinking(message2.get("content"))
    await db.save_turn(user_id, {"role": "assistant", "content": answer})
    await db.log(
        user_id,
        "INFO",
        "bot_response",
        {"latency_ms": (time.monotonic() - turn_started_at) * 1000},
    )
    return answer
