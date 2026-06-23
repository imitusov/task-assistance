"""Pre-development verification: NeuralDeep (Qwen) OpenAI tool-calling probe.

Run manually before application code. Verifies that
`NEURALDEEP_API_URL/chat/completions` accepts OpenAI-format tool calling and
returns `tool_calls`, and documents whether `<think>...</think>` reasoning
blocks appear in the response (and where, relative to `tool_calls`).

Exit 0 on PASS, exit 1 on any assertion failure, unexpected response format,
or network error. Prints the full raw response and a final PASS/FAIL line.
"""

import json
import os
import sys

import httpx

# --- minimal .env loader (standalone script; not an application module) ---
def _load_env() -> None:
    path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_MODEL = "qwen3.6-35b-a3b"

_DUMMY_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City name"},
            },
            "required": ["city"],
        },
    },
}


def _contains_think(text) -> bool:
    return isinstance(text, str) and "<think>" in text


def main() -> int:
    _load_env()
    api_key = os.environ.get("NEURALDEEP_API_KEY")
    api_url = os.environ.get("NEURALDEEP_API_URL")
    if not api_key or not api_url:
        print("FAIL: NEURALDEEP_API_KEY / NEURALDEEP_API_URL not set")
        return 1

    url = f"{api_url.rstrip('/')}/chat/completions"
    payload = {
        "model": _MODEL,
        "messages": [
            {"role": "user", "content": "What's the weather in Paris right now?"}
        ],
        "tools": [_DUMMY_TOOL],
        "tool_choice": "auto",
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=30.0)
    except httpx.HTTPError as exc:
        print(f"FAIL: network error: {exc!r}")
        return 1

    print(f"HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError:
        print("RAW (non-JSON):")
        print(resp.text)
        print("FAIL: response was not valid JSON")
        return 1

    print("RAW RESPONSE:")
    print(json.dumps(data, indent=2, ensure_ascii=False))

    if resp.status_code != 200:
        print(f"FAIL: non-200 status ({resp.status_code})")
        return 1

    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        print("FAIL: unexpected response shape (no choices[0].message)")
        return 1

    tool_calls = message.get("tool_calls")
    content = message.get("content")
    reasoning = message.get("reasoning_content") or message.get("reasoning")

    # --- findings: thinking-token behaviour ---
    print("\n--- FINDINGS ---")
    think_in_content = _contains_think(content)
    think_in_reasoning_field = reasoning is not None
    print(f"tool_calls present:            {bool(tool_calls)}")
    print(f"<think> in message.content:    {think_in_content}")
    print(f"separate reasoning_* field:    {think_in_reasoning_field}")
    if think_in_content:
        print("  -> position: inside message.content (alongside/around tool_calls)")
        print("  -> llm.py MUST strip <think>...</think> from content.")
    elif think_in_reasoning_field:
        print("  -> reasoning lives in a separate field, NOT in content.")
        print("  -> nothing to strip from content; _strip_thinking is a no-op.")
    else:
        print("  -> no <think> blocks observed in this response.")
        print("  -> _strip_thinking is a safe no-op for this endpoint.")

    # --- pass criterion: tool calling works ---
    if not tool_calls:
        print("\nFAIL: response did not contain tool_calls")
        return 1

    print("\nPASS: tool calling works; thinking-token finding documented above")
    return 0


if __name__ == "__main__":
    sys.exit(main())
