import json
import logging
from datetime import datetime, timezone

import config

_REDACTED = "***REDACTED***"
_SENSITIVE_MARKERS = ("token", "api_key", "secret", "password")

# Known non-secret keys that would otherwise be caught by the substring match
# above (e.g. "token_count" contains "token"). The spec's Log Events table
# mandates this exact field name for llm_call's numeric token usage, so it
# must survive sanitize() as a number, not "***REDACTED***" (which would also
# break the documented Grafana query `data->>'token_count' ... ::int`).
_SAFE_KEYS = {"token_count"}

_LEVEL_RANKS = logging.getLevelNamesMapping()


def sanitize(data: dict) -> dict:
    result = {}
    for key, value in data.items():
        if key not in _SAFE_KEYS and any(
            marker in key.lower() for marker in _SENSITIVE_MARKERS
        ):
            result[key] = _REDACTED
        else:
            result[key] = value
    return result


def log_stdout(level, event, user_id, data) -> None:
    if _LEVEL_RANKS[level] < _LEVEL_RANKS[config.LOG_LEVEL]:
        return
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "event": event,
        "user_id": user_id,
        "data": sanitize(data),
    }
    print(json.dumps(payload))
