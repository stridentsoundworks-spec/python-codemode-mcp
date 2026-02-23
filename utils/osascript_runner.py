"""Shared AppleScript execution helper.

Used by both tools/osascript.py (raw execution) and tools/iterm2.py
(iTerm2-specific AppleScript commands). Single implementation, no duplication.
"""

import subprocess


def run(script: str, timeout: int = 10) -> tuple[bool, str]:
    """Execute an AppleScript string via osascript.

    Args:
        script: The AppleScript source to execute.
        timeout: Max seconds to wait. Default: 10.

    Returns:
        (success: bool, output_or_error: str)
    """
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode == 0:
            return True, r.stdout.strip()
        return False, r.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, f"AppleScript timed out after {timeout}s"
    except Exception as e:
        return False, str(e)


def escape(text: str) -> str:
    """Escape a string for safe embedding in AppleScript double-quotes."""
    return (
        text
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
