import os

from dotenv import load_dotenv

# Load a local .env if present. override=False so already-set environment
# variables (Railway's injected vars in prod, the test suite's monkeypatched
# vars in CI/tests) always win over the file — and it's a silent no-op when no
# .env exists (e.g. on Railway).
load_dotenv(override=False)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
NEURALDEEP_API_KEY = os.environ["NEURALDEEP_API_KEY"]
NEURALDEEP_API_URL = os.environ["NEURALDEEP_API_URL"]
DATABASE_URL = os.environ["DATABASE_URL"]
SECRET_KEY = os.environ["SECRET_KEY"]
LOG_LEVEL = os.environ["LOG_LEVEL"]

MCP_TOOLS_TTL = int(os.environ.get("MCP_TOOLS_TTL", 86400))
MAX_HISTORY_MESSAGES = int(os.environ.get("MAX_HISTORY_MESSAGES", 20))
MAX_TOOL_RESULT_CHARS = int(os.environ.get("MAX_TOOL_RESULT_CHARS", 8000))
MAX_TOOL_ROUNDS = int(os.environ.get("MAX_TOOL_ROUNDS", 3))
DESTRUCTIVE_TOOLS = {
    name.strip()
    for name in os.environ.get("DESTRUCTIVE_TOOLS", "delete-object").split(",")
    if name.strip()
}
CONVERSATION_RETENTION_DAYS = int(os.environ.get("CONVERSATION_RETENTION_DAYS", 7))
LOG_RETENTION_DAYS = int(os.environ.get("LOG_RETENTION_DAYS", 30))

LLM_MODEL = os.environ.get("LLM_MODEL", "qwen3.6-35b-a3b")
MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "https://ai.todoist.net/mcp")
TODOIST_BASE_URL = os.environ.get("TODOIST_BASE_URL", "https://api.todoist.com/api/v1")
