import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

import config
import db
import mcp
import messages


@dataclass
class ConfirmationRequired:
    """Returned by `process_message` instead of an answer string when a
    destructive tool call (per `config.DESTRUCTIVE_TOOLS`) needs explicit
    user confirmation before it runs. `context` is an opaque, JSON-shaped
    dict that the caller must pass unchanged to `resume_tool_call` on
    confirmation, or simply discard on cancellation (nothing has been
    persisted or executed yet)."""

    description: str
    context: dict

_MODEL = config.LLM_MODEL
_LLM_TIMEOUT = httpx.Timeout(30.0)

_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"

_SYSTEM_PROMPTS = {
    "en": (
        "You are a helpful Todoist assistant. Today's date is {current_date} "
        "(UTC). Help the user manage and answer questions about their Todoist "
        "tasks using the available tools. Be concise and direct. If a tool "
        "result indicates an error, read the error and retry with a corrected "
        "call rather than giving up."
    ),
    "ru": (
        "Ты — полезный ассистент Todoist. Сегодняшняя дата: {current_date} "
        "(UTC). Помогай пользователю управлять задачами Todoist и отвечать на "
        "вопросы о них, используя доступные инструменты. Отвечай кратко и по "
        "делу. Если результат инструмента содержит ошибку, прочитай её и "
        "повтори вызов с исправленными параметрами, а не сдавайся."
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


_TRUNCATION_MARKER = "…[truncated]"


def _truncate_tool_result(content: str) -> str:
    cap = config.MAX_TOOL_RESULT_CHARS
    if len(content) <= cap:
        return content
    return content[: cap - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER


def _describe_tool_calls(tool_calls: list[dict]) -> str:
    parts = []
    for tool_call in tool_calls:
        name = tool_call["function"]["name"]
        arguments = json.loads(tool_call["function"]["arguments"])
        args_str = ", ".join(f"{k}={v}" for k, v in arguments.items())
        parts.append(f"{name}({args_str})")
    return "; ".join(parts)


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


async def _call_llm(
    messages_payload: list[dict],
    tools: list,
    user_id: int,
    tool_choice: str = "auto",
):
    payload = {
        "model": _MODEL,
        "messages": messages_payload,
        "tools": tools,
        "tool_choice": tool_choice,
        # Stable per-user id → NeuralDeep routes the user to the same upstream,
        # keeping the prompt KV-cache warm across turns (faster multi-turn).
        "user": str(user_id),
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


async def _run_llm_call(
    stage: str,
    messages_payload: list[dict],
    tools: list,
    user_id: int,
    language: str,
    turn_start: datetime,
    cleanup_on_failure: bool,
    tool_choice: str = "auto",
) -> tuple[dict | None, str | None]:
    """Makes one LLM call and handles its timeout/429/logging boilerplate.

    Returns `(message, None)` on success or `(None, early_return_answer)` on
    timeout or rate limit. `cleanup_on_failure` controls whether
    `delete_turn_tool_results(user_id, turn_start)` runs before an early
    return — `True` once any tool results may already be persisted this turn,
    `False` for the very first call (nothing to clean up yet).
    """
    try:
        response, latency_ms = await _call_llm(
            messages_payload, tools, user_id, tool_choice=tool_choice
        )
    except httpx.TimeoutException:
        if cleanup_on_failure:
            await db.delete_turn_tool_results(user_id, turn_start)
        await db.log(user_id, "WARNING", "llm_call", {"stage": stage, "error": "timeout"})
        return None, messages.get("llm_timeout", language)

    if response.status_code == 429:
        if cleanup_on_failure:
            await db.delete_turn_tool_results(user_id, turn_start)
        return None, await _handle_rate_limit(user_id, response, language)

    response.raise_for_status()
    payload = response.json()
    message = payload["choices"][0]["message"]
    await db.log(
        user_id,
        "INFO",
        "llm_call",
        {
            "stage": stage,
            "latency_ms": latency_ms,
            "token_count": _extract_token_count(payload),
        },
    )
    return message, None


async def _execute_tool_calls(
    user_id: int,
    token: str,
    language: str,
    turn_start: datetime,
    tool_calls: list[dict],
    conversation: list[dict],
) -> str | None:
    """Executes each tool call in order, appending `{"role": "tool", ...}`
    results to `conversation`. Returns `None` on success, or the
    `tool_failure` message (after the ERROR log + `delete_turn_tool_results`
    cleanup, Rule 6/14) on the first `mcp.call_tool` / `db.save_tool_result`
    exception — remaining calls in `tool_calls` are then skipped. An
    `isError: true` result is not an exception and does not stop the loop."""
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
            "content": _truncate_tool_result(json.dumps(result)),
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
    return None


async def _run_tool_loop(
    user_id: int,
    token: str,
    language: str,
    turn_start: datetime,
    turn_started_at: float,
    conversation: list[dict],
    tools: list,
    system_message: dict,
    start_round: int,
    requested_tools: bool,
) -> str | ConfirmationRequired:
    """The bounded agentic tool loop, shared by `process_message` (starting
    at round 1) and `resume_tool_call` (continuing at `round_num + 1` after a
    confirmed destructive call has already been executed)."""

    async def _final_answer(content: str | None) -> str:
        answer = _strip_thinking(content)
        await db.save_turn(user_id, {"role": "assistant", "content": answer})
        await db.log(
            user_id,
            "INFO",
            "bot_response",
            {"latency_ms": (time.monotonic() - turn_started_at) * 1000},
        )
        return answer

    for round_num in range(start_round, config.MAX_TOOL_ROUNDS + 1):
        message, early_return = await _run_llm_call(
            f"tool_round_{round_num}",
            [system_message] + conversation,
            tools,
            user_id,
            language,
            turn_start,
            cleanup_on_failure=requested_tools,
        )
        if early_return is not None:
            return early_return

        content = message.get("content")
        tool_calls = message.get("tool_calls")

        if not tool_calls:
            return await _final_answer(content)

        destructive = [
            tc for tc in tool_calls if tc["function"]["name"] in config.DESTRUCTIVE_TOOLS
        ]
        if destructive:
            assistant_tool_msg = {
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            }
            return ConfirmationRequired(
                description=_describe_tool_calls(destructive),
                context={
                    "conversation": list(conversation),
                    "assistant_tool_msg": assistant_tool_msg,
                    "tool_calls": tool_calls,
                    "tools": tools,
                    "round_num": round_num,
                },
            )

        requested_tools = True
        assistant_tool_msg = {
            "role": "assistant",
            "content": content,
            "tool_calls": tool_calls,
        }
        await db.save_assistant_tool_call(user_id, assistant_tool_msg)
        conversation.append(assistant_tool_msg)

        failure = await _execute_tool_calls(
            user_id, token, language, turn_start, tool_calls, conversation
        )
        if failure is not None:
            return failure

    # Exhaustion: the model still wanted tools after MAX_TOOL_ROUNDS — force
    # one final answer-only call rather than returning bare retry narration.
    message, early_return = await _run_llm_call(
        "answer_generation",
        [system_message] + conversation,
        tools,
        user_id,
        language,
        turn_start,
        cleanup_on_failure=requested_tools,
        tool_choice="none",
    )
    if early_return is not None:
        return early_return

    return await _final_answer(message.get("content"))


async def process_message(
    user_id: int,
    user_text: str,
    token: str,
    language: str,
    turn_start: datetime,
) -> str | ConfirmationRequired:
    turn_started_at = time.monotonic()

    history = await db.get_history(user_id)
    system_message = _build_system_message(language)
    user_msg = {"role": "user", "content": user_text}
    await db.save_user_message(user_id, user_msg)
    conversation = list(history) + [user_msg]
    tools = await mcp.get_tools(token)

    return await _run_tool_loop(
        user_id,
        token,
        language,
        turn_start,
        turn_started_at,
        conversation,
        tools,
        system_message,
        start_round=1,
        requested_tools=False,
    )


async def resume_tool_call(
    user_id: int,
    token: str,
    language: str,
    turn_start: datetime,
    context: dict,
) -> str | ConfirmationRequired:
    """Executes a previously-deferred destructive tool call (per
    `ConfirmationRequired.context`) after explicit user confirmation, then
    resumes the bounded tool loop from the next round."""
    turn_started_at = time.monotonic()

    conversation = context["conversation"]
    assistant_tool_msg = context["assistant_tool_msg"]
    tool_calls = context["tool_calls"]
    tools = context["tools"]
    round_num = context["round_num"]
    system_message = _build_system_message(language)

    await db.save_assistant_tool_call(user_id, assistant_tool_msg)
    conversation.append(assistant_tool_msg)

    failure = await _execute_tool_calls(
        user_id, token, language, turn_start, tool_calls, conversation
    )
    if failure is not None:
        return failure

    return await _run_tool_loop(
        user_id,
        token,
        language,
        turn_start,
        turn_started_at,
        conversation,
        tools,
        system_message,
        start_round=round_num + 1,
        requested_tools=True,
    )
