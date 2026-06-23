import json
import logging
from datetime import datetime, timezone

import config

_REDACTED = "***REDACTED***"
_SENSITIVE_MARKERS = ("token", "api_key", "secret", "password")

_LEVEL_RANKS = logging.getLevelNamesMapping()


def sanitize(data: dict) -> dict:
    result = {}
    for key, value in data.items():
        if any(marker in key.lower() for marker in _SENSITIVE_MARKERS):
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
