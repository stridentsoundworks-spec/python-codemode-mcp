"""iTerm2 terminal control tools.

Provides async-safe wrappers around iTerm2 AppleScript automation.
All tools follow the impl/register pattern:
    - impl_*() — module-level sync function, used directly by compose stubs
    - register() — wraps impl_* as MCP tools on the server

Tools:
    iterm2_read        — read last N lines from a session
    iterm2_write       — send a command and wait for output to settle
    iterm2_send_control — send a control character (Ctrl-C, Ctrl-D, etc.)
    iterm2_send_text   — send text without Enter (for interactive prompts)
    iterm2_cwd         — get the current working directory

Requires:
    - macOS with iTerm2 installed
    - iTerm2 Shell Integration for iterm2_cwd Method 1
      https://iterm2.com/documentation-shell-integration.html
"""

import subprocess
import time

from mcp.server.fastmcp import FastMCP
from utils.osascript_runner import run, escape
from utils.logger import log_tool_call


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_LINES = 200         # hard cap on lines readable in one call
MAX_OUTPUT_LINES = 100  # cap for buffer-shrink guard
DEFAULT_READ_LINES = 50


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_tab_target(tab: int | None) -> str:
    """Return AppleScript tab target string."""
    if tab is None:
        return "current session of current window"
    return f"session 1 of tab {tab} of current window"


def _get_contents(tab: int | None) -> tuple[bool, str]:
    """Read all visible text from an iTerm2 session."""
    target = _get_tab_target(tab)
    script = f'tell application "iTerm2" to tell {target} to return contents'
    return run(script)


# ---------------------------------------------------------------------------
# Implementations (module-level — used by both MCP registration and compose stubs)
# ---------------------------------------------------------------------------

def impl_iterm2_read(lines: int = DEFAULT_READ_LINES, tab: int | None = None) -> str:
    """Read the last N lines from an iTerm2 session.

    Args:
        lines: Number of lines to return. Max 200. Default 50.
        tab: Tab index (1-based). None = current tab.

    Returns:
        Terminal output as a string.
    """
    with log_tool_call("iterm2_read", lines=lines, tab=tab) as log:
        lines = min(lines, MAX_LINES)
        ok, contents = _get_contents(tab)
        if not ok:
            log["result_preview"] = contents[:200]
            return f"Error reading terminal: {contents}"
        all_lines = contents.split("\n")
        result = "\n".join(all_lines[-lines:])
        log["result_preview"] = result[:200]
        return result


def impl_iterm2_write(
    text: str,
    wait: bool = True,
    timeout: int = 8,
    tab: int | None = None,
) -> str:
    """Write text or a command to an iTerm2 session.

    Sends text followed by Enter, then waits for output to settle
    (3 consecutive stable readings 0.3s apart).

    Args:
        text: Command or text to send.
        wait: Whether to wait for output to settle. Default True.
        timeout: Max seconds to wait. Default 8.
        tab: Tab index (1-based). None = current tab.

    Returns:
        New output produced after the command, as a string.
    """
    with log_tool_call("iterm2_write", text=text, wait=wait, timeout=timeout, tab=tab) as log:
        target = _get_tab_target(tab)

        # Snapshot before
        ok, before = _get_contents(tab)
        before_lines = before.split("\n") if ok else []

        # Send the command
        escaped = escape(text)
        send_script = f'tell application "iTerm2" to tell {target} to write text "{escaped}"'
        ok, err = run(send_script)
        if not ok:
            log["result_preview"] = err[:200]
            return f"Error sending command: {err}"

        if not wait:
            log["result_preview"] = "(sent, not waiting)"
            return "(sent, not waiting for output)"

        # Wait for output to settle
        deadline = time.time() + timeout
        stable_count = 0
        prev_contents = ""

        while time.time() < deadline:
            time.sleep(0.3)
            ok, contents = _get_contents(tab)
            if not ok:
                continue
            if contents == prev_contents:
                stable_count += 1
                if stable_count >= 3:
                    break
            else:
                stable_count = 0
                prev_contents = contents

        ok, after = _get_contents(tab)
        if not ok:
            log["result_preview"] = after[:200]
            return f"Command sent but could not read output: {after}"

        after_lines = after.split("\n")

        # Buffer-shrink guard: if screen was cleared, line count drops below before
        if len(after_lines) < len(before_lines):
            output = "\n".join(after_lines).strip()
            if len(after_lines) > MAX_OUTPUT_LINES:
                after_lines = after_lines[-MAX_OUTPUT_LINES:]
                return f"(screen cleared, showing last {MAX_OUTPUT_LINES} lines)\n" + "\n".join(after_lines)
            return output if output else "(no output)"

        new_lines = after_lines[len(before_lines):]
        result = "\n".join(new_lines).strip()
        log["result_preview"] = result[:200]
        return result if result else "(no new output)"


def impl_iterm2_send_control(character: str, tab: int | None = None) -> str:
    """Send a control character to an iTerm2 session.

    Args:
        character: Single character, e.g. 'C' for Ctrl-C, 'D' for Ctrl-D.
        tab: Tab index (1-based). None = current tab.

    Returns:
        Confirmation string.
    """
    with log_tool_call("iterm2_send_control", character=character, tab=tab) as log:
        target = _get_tab_target(tab)
        code = ord(character.upper()) - ord('A') + 1
        script = (
            f'tell application "iTerm2" to tell {target} '
            f'to write text (ASCII character {code})'
        )
        ok, result = run(script)
        res = f"Sent Ctrl-{character.upper()}" if ok else f"Error: {result}"
        log["result_preview"] = res
        return res


def impl_iterm2_send_text(text: str, tab: int | None = None) -> str:
    """Send text to an iTerm2 session WITHOUT pressing Enter.

    Useful for interactive prompts (y/n, passwords, etc.).

    Args:
        text: Text to send.
        tab: Tab index (1-based). None = current tab.

    Returns:
        Confirmation string.
    """
    with log_tool_call("iterm2_send_text", text=text, tab=tab) as log:
        target = _get_tab_target(tab)
        escaped = escape(text)
        script = (
            f'tell application "iTerm2" to tell {target} '
            f'to write text "{escaped}" newline NO'
        )
        ok, result = run(script)
        res = f'Sent text (no Enter): "{text}"' if ok else f"Error: {result}"
        log["result_preview"] = res
        return res


def impl_iterm2_cwd(tab: int | None = None) -> str:
    """Get the current working directory of an iTerm2 session.

    Tries three methods in order:
        1. iTerm2 Shell Integration variable (\\$ITERM_SESSION_ID)
        2. lsof PID-based CWD lookup
        3. Returns error (Method 3 removed — was destructive)

    Args:
        tab: Tab index (1-based). None = current tab.

    Returns:
        Absolute path string, or error message.
    """
    with log_tool_call("iterm2_cwd", tab=tab) as log:
        target = _get_tab_target(tab)

        # Method 1: Shell Integration variable
        script = f'tell application "iTerm2" to tell {target} to return variable named "session.path"'
        ok, result = run(script)
        if ok and result and result != "missing value":
            log["result_preview"] = result[:200]
            return result

        # Method 2: PID-based lsof CWD lookup
        tty_script = f'tell application "iTerm2" to tell {target} to return tty'
        ok, tty = run(tty_script)
        if ok and tty:
            try:
                r = subprocess.run(
                    ["lsof", "-t", tty.strip()],
                    capture_output=True, text=True, timeout=5
                )
                pids = [p.strip() for p in r.stdout.strip().split() if p.strip()]
                for pid in pids:
                    r2 = subprocess.run(
                        ["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"],
                        capture_output=True, text=True, timeout=5
                    )
                    for line in r2.stdout.strip().split():
                        if line.startswith("n"):
                            result = line[1:]
                            log["result_preview"] = result[:200]
                            return result
            except (subprocess.TimeoutExpired, Exception):
                pass

        # Method 3 intentionally removed — it typed `pwd` into the foreground
        # process, which was destructive if a REPL or server was running.
        err = (
            "Error: could not determine working directory. "
            "Shell integration (Method 1) and lsof (Method 2) both failed. "
            "Ensure iTerm2 Shell Integration is installed: "
            "https://iterm2.com/documentation-shell-integration.html"
        )
        log["result_preview"] = err[:200]
        return err


# ---------------------------------------------------------------------------
# MCP Registration
# ---------------------------------------------------------------------------

def register(mcp: FastMCP) -> None:
    """Register iTerm2 tools on the MCP server."""

    @mcp.tool()
    def iterm2_read(lines: int = DEFAULT_READ_LINES, tab: int | None = None) -> str:
        """Read the last N lines from an iTerm2 session."""
        return impl_iterm2_read(lines=lines, tab=tab)

    @mcp.tool()
    def iterm2_write(
        text: str,
        wait: bool = True,
        timeout: int = 8,
        tab: int | None = None,
    ) -> str:
        """Write text or a command to an iTerm2 session and wait for output."""
        return impl_iterm2_write(text=text, wait=wait, timeout=timeout, tab=tab)

    @mcp.tool()
    def iterm2_send_control(character: str, tab: int | None = None) -> str:
        """Send a control character (e.g. 'C' for Ctrl-C) to an iTerm2 session."""
        return impl_iterm2_send_control(character=character, tab=tab)

    @mcp.tool()
    def iterm2_send_text(text: str, tab: int | None = None) -> str:
        """Send text to an iTerm2 session WITHOUT pressing Enter."""
        return impl_iterm2_send_text(text=text, tab=tab)

    @mcp.tool()
    def iterm2_cwd(tab: int | None = None) -> str:
        """Get the current working directory of an iTerm2 session."""
        return impl_iterm2_cwd(tab=tab)
