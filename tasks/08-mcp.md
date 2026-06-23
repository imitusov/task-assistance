# Task: Implement `mcp.py`

## Product context
This is a personal Telegram bot that lets multiple independent users manage and
query their own Todoist tasks through natural conversation, powered by an open
LLM (Qwen) with tool calling, hosted on Railway with observability via Grafana.
This module is the **client for the official Todoist MCP server**
(`https://ai.todoist.net/mcp`): it fetches Todoist's tool definitions, converts
them to OpenAI function-calling format for the LLM, caches them per user token,
and executes tool calls on the user's behalf. Each user authenticates to the MCP
server with their own personal Todoist API token (Bearer header), so all caching
and calls are strictly per-token to keep users isolated.

## Build order position
This is module 8 of 11. The modules listed under "Already implemented" below are
complete and tested. Do not modify them. (`llm.py`, module 9, will consume this
module's output — do not implement it here.)

## Already-implemented interfaces
- `config.py`:
  - `MCP_TOOLS_TTL: int` — default `86400` (cache TTL in seconds, 24h)
  - Read all config from `config` — never from `os.environ`.
- `logger.py`:
  - `sanitize(data: dict) -> dict` — top-level key redaction; returns a new dict.
  - `log_stdout(level: str, event: str, user_id: int | None, data: dict) -> None`
    — single JSON line to stdout; never raises.

(`mcp.py` depends only on `config` and `logger`. It does NOT use `db.py` and
must not import it.)

## Database tables used
None. This module performs no database access — it is a pure external
integration with an in-memory per-token cache. All SQL stays in `db.py`.

## MCP server connection facts (brief → MCP Server)
```
Endpoint:  https://ai.todoist.net/mcp
Protocol:  JSON-RPC 2.0, responses in SSE format
Auth:      Authorization: Bearer <user's Todoist API token>
Headers:   Content-Type: application/json
           Accept: application/json, text/event-stream
```
Responses come back as Server-Sent Events, not plain JSON — the SSE body must be
parsed before the JSON payload is available.

## Module contract
(technical-spec.md → mcp.py)

**SSE parsing:** Find the `data:` line, strip the `data:` prefix (and surrounding
whitespace), decode the remaining JSON. If no `data:` line is present, raise a
descriptive error whose message contains the raw response text.

**Schema conversion (MCP → OpenAI):** Rename `inputSchema` → `parameters`, wrap
the tool in `{"type": "function", "function": {...}}`. Pass all other fields
(`name`, `description`, nested schema properties) through unchanged. An empty
`inputSchema` produces empty `parameters`.

**Cache (per token):** Return the cached tools if younger than `MCP_TOOLS_TTL`.
Otherwise fetch from the server, convert to OpenAI format, cache, and return.
The **converted** OpenAI format is returned on BOTH cache hit and cache miss —
never raw MCP format. Different tokens hold independent cache entries.

- `get_tools(token: str) -> list` — OpenAI-format tool list, cached per token.
- `call_tool(token: str, name: str, arguments: dict) -> dict` — returns the
  `result` field from the JSON-RPC response. Uses `httpx.Timeout(total=10.0)`
  for full SSE receipt. Raises on failure or timeout.
- `evict_cache(token: str) -> None` — removes the token's entry from the cache
  silently (no error if absent).

## Relevant error handling rules
(technical-spec.md → Error Handling Rules — only those touching this module)

- **Rule 1** — External failures must surface, never be silent. `get_tools` and
  `call_tool` raise descriptive errors on HTTP error status, SSE parse failure,
  or timeout; the caller turns these into a user-facing message.
- **Rule 6** — On MCP failure (or a later `save_tool_result` failure) the caller
  (`llm.py`) runs `delete_turn_tool_results`, returns `tool_failure`, and
  discards partial results. `mcp.py`'s job is therefore to **raise** on any tool
  failure/timeout rather than return partial or malformed data — do not swallow.

(Rule 4, HTTP 429 → rate-limit message, is applied by the caller, not here. This
module only needs to surface the failure.)

## Test cases
(technical-spec.md → tests/test_mcp.py) — no real network calls; mock httpx and
`time.time`.

**SSE parser:**
- Valid SSE with a `data:` line returns the parsed JSON.
- No `data:` line raises a descriptive error containing the raw text.
- Multiple lines: only the `data:` line content is returned.
- Leading/trailing whitespace on the `data:` line is stripped.

**Tool schema converter:**
- `inputSchema` is renamed to `parameters`.
- Result is wrapped in `{"type": "function", "function": {...}}`.
- `name` and `description` pass through unchanged.
- Nested schema properties pass through unchanged.
- Empty `inputSchema` produces empty `parameters`.

**Tool cache:**
- `get_tools(token)` fetches on the first call.
- `get_tools(token)` returns cached data on the second call (HTTP not called).
- Cache expires after `MCP_TOOLS_TTL` (mock `time.time`).
- After TTL expiry, returned tools are in OpenAI format
  (`{"type": "function", "function": {...}}`), NOT raw MCP format
  (`{"name": ..., "inputSchema": ...}`) — guards the cache-miss-conversion
  regression.
- `evict_cache(token)` causes the next `get_tools` to fetch again.
- Different tokens have independent cache entries.

**`call_tool`:**
- Returns the `result` field from a valid JSON-RPC SSE response.
- Raises on HTTP error status.
- Raises on SSE parse failure.
- Raises on timeout (mock httpx timeout).
- Uses `httpx.Timeout(total=10.0)` — assert the timeout parameter on the request.

## Expected output
- A file `mcp.py` implementing the contract exactly
- A file `tests/test_mcp.py` implementing every test case above
- All tests passing (≥80% coverage)
- After tests pass: append `mcp.py`'s public signatures (`get_tools`,
  `call_tool`, `evict_cache`, plus the SSE-parser and schema-converter helpers
  if public) to `interfaces.md`

## Agent instructions
1. FIRST write `tests/test_mcp.py` from the test cases above. Mock all external
   services with `unittest.mock` (no real network calls) and mock `time.time`
   for the cache-expiry cases. Do not write implementation yet.
2. Run the tests. They must FAIL (red) — nothing is implemented.
3. THEN write `mcp.py` to satisfy the contract.
4. Run the tests again. Iterate until all PASS (green) and coverage is met.
5. Match the contract signatures EXACTLY — names, parameters, return types.
6. Call already-implemented interfaces as given (`config.MCP_TOOLS_TTL`,
   `logger.sanitize`, `logger.log_stdout`) — do not reimplement them.
7. Handle every error per the rules above — raise descriptive errors on HTTP
   error, SSE parse failure, or timeout; never return partial/raw MCP data.
8. Do not add dependencies not already in the project (use `httpx` and the
   stdlib only); send the Bearer + `Accept: application/json, text/event-stream`
   headers per the connection facts; use `httpx.Timeout(total=10.0)` in
   `call_tool`.
9. Do not modify any other module; do not import `db.py` or `llm.py`.
10. When all tests pass, append public signatures to `interfaces.md`.
11. If the contract is ambiguous or conflicts with an interface, STOP and ask —
    do not guess.

Activate `.venv` before any `pytest`/`python`/`pip` command.
