"""Structured JSON logger for MCP tool calls."""

import json
import logging
import time
from contextlib import contextmanager
from pathlib import Path

LOG_DIR = Path(__file__).parent.parent / "logs" / "mcp"
LOG_DIR.mkdir(parents=True, exist_ok=True)

_logger = logging.getLogger("mcp-tools")
_handler = logging.FileHandler(LOG_DIR / "tools.jsonl")
_handler.setFormatter(logging.Formatter("%(message)s"))
_logger.addHandler(_handler)
_logger.setLevel(logging.INFO)


@contextmanager
def log_tool_call(tool_name: str, **params):
    """Context manager that logs tool call start, duration, and result/error.

    Usage:
        with log_tool_call("iterm2_write", text="ls -la") as log:
            result = do_the_thing()
            log["result_preview"] = result[:200]

    Log output: logs/mcp/tools.jsonl (one JSON object per line)
    """
    entry = {
        "tool": tool_name,
        "params": {k: str(v)[:200] for k, v in params.items()},
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    start = time.monotonic()
    try:
        yield entry
        entry["status"] = "ok"
    except Exception as e:
        entry["status"] = "error"
        entry["error"] = f"{type(e).__name__}: {e}"
        raise
    finally:
        entry["duration_ms"] = round((time.monotonic() - start) * 1000)
        _logger.info(json.dumps(entry))
