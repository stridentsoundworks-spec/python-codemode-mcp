"""Raw AppleScript execution tool.

Provides a thin wrapper around the shared osascript runner.
Supports arbitrary AppleScript with configurable timeout.

Follows the impl/register pattern:
    impl_osascript_run() — module-level, used by compose stubs
    register()           — wraps as MCP tool
"""

from mcp.server.fastmcp import FastMCP
from utils.osascript_runner import run
from utils.logger import log_tool_call


def impl_osascript_run(script: str, timeout: int = 10) -> str:
    """Execute arbitrary AppleScript code.

    Runs the provided AppleScript via osascript and returns the output.
    Use for macOS automation: controlling apps, reading system info,
    showing dialogs, file operations, etc.

    Args:
        script: The AppleScript source code to execute.
        timeout: Max seconds to wait for execution. Default: 10.

    Returns:
        Script output as a string, or an error message.
    """
    with log_tool_call("osascript_run", script=script, timeout=timeout) as log:
        ok, result = run(script, timeout=timeout)
        if ok:
            final = result if result else "(script executed successfully, no output)"
            log["result_preview"] = final[:200]
            return final
        log["result_preview"] = result[:200]
        return f"Error: {result}"


def register(mcp: FastMCP) -> None:
    """Register the osascript tool on the MCP server."""

    @mcp.tool()
    def osascript_run(script: str, timeout: int = 10) -> str:
        """Execute arbitrary AppleScript code.

        Use for macOS automation: controlling apps, reading system info,
        showing dialogs, file operations, etc.

        Args:
            script: The AppleScript source code to execute.
            timeout: Max seconds to wait for execution. Default: 10.
        """
        return impl_osascript_run(script, timeout=timeout)
