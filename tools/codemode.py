"""Code Mode — MCP Compose Tool.

Exposes a single `compose` tool that executes a Python script with all
MCP tool stubs injected as callable functions. Enables an LLM to compose
multiple tool calls in one round-trip instead of sequential ping-pong.

Pattern inspired by Cloudflare's Code Mode (Feb 2026):
https://blog.cloudflare.com/code-mode-mcp/
Adapted for local Python MCP architecture.

Architecture:
    Phase 1: Stub registry — sync tool functions wrapped in asyncio executor
    Phase 2: Sandboxed exec() with whitelist namespace
    Phase 3: MCP registration via register(mcp) pattern

Threat model:
    This sandbox is a GUARDRAIL, not a security boundary.
    It catches accidental os.system() calls and LLM hallucinations.
    It does NOT defend against intentional exploitation via object
    introspection chains. The real security model is: the LLM generates
    compose scripts in a local-agent, trusted-user environment.
    If compose ever accepts code from untrusted sources, this needs
    OS-level sandboxing (subprocess isolation, seccomp, etc.).
"""

import asyncio
import functools
import io
import json
import math
import re
import textwrap
import traceback
from datetime import datetime
from typing import Callable
import ast

from mcp.server.fastmcp import FastMCP
from utils.logger import log_tool_call

from tools.iterm2 import (
    impl_iterm2_read,
    impl_iterm2_write,
    impl_iterm2_send_control,
    impl_iterm2_send_text,
    impl_iterm2_cwd,
)
from tools.osascript import impl_osascript_run


# ---------------------------------------------------------------------------
# Feature flag — set False to disable compose without breaking other tools
# ---------------------------------------------------------------------------
COMPOSE_ENABLED = True


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
EXECUTOR_TIMEOUT = 30   # seconds
MAX_OUTPUT = 50_000     # characters — stdout cap


# ---------------------------------------------------------------------------
# Safe getattr/hasattr — blocks dunder introspection chains at runtime
# ---------------------------------------------------------------------------

def _safe_getattr(obj, name, *default):
    """getattr() that blocks dunder attribute access."""
    if isinstance(name, str) and name.startswith("__") and name.endswith("__"):
        raise AttributeError(
            f"Access to dunder attribute '{name}' is blocked in compose sandbox"
        )
    return getattr(obj, name, *default) if default else getattr(obj, name)


def _safe_hasattr(obj, name):
    """hasattr() that blocks dunder attribute access."""
    if isinstance(name, str) and name.startswith("__") and name.endswith("__"):
        return False
    return hasattr(obj, name)


class _DunderChecker(ast.NodeVisitor):
    """Reject any attribute access to dunder names in compose scripts at parse time."""

    def visit_Attribute(self, node):
        if node.attr.startswith("__") and node.attr.endswith("__"):
            raise SyntaxError(
                f"Access to dunder attribute '{node.attr}' is blocked in compose sandbox",
                ("<compose>", node.lineno, node.col_offset, None)
            )
        self.generic_visit(node)


def _validate_ast(code: str) -> str | None:
    """Parse code and check for dunder attribute access.

    Returns None if clean, or an error string if blocked.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        line = e.lineno or "?"
        return f"❌ SyntaxError at line {line}: {e.msg}"

    try:
        _DunderChecker().visit(tree)
    except SyntaxError as e:
        return f"🚫 {e.msg}"

    return None


# ---------------------------------------------------------------------------
# Sandbox globals — whitelist namespace injected into every compose script
# ---------------------------------------------------------------------------
# asyncio is cherry-picked: only gather/sleep/wait_for are exposed.
# This blocks create_subprocess_shell / create_subprocess_exec escapes.
# ---------------------------------------------------------------------------

SANDBOX_GLOBALS = {
    "__builtins__": {
        # I/O (overridden at exec time to capture output)
        "print": print,

        # Iteration & ranges
        "len": len,
        "range": range,
        "enumerate": enumerate,
        "zip": zip,
        "map": map,
        "filter": filter,
        "sorted": sorted,
        "reversed": reversed,

        # Types & constructors
        "list": list,
        "dict": dict,
        "str": str,
        "int": int,
        "float": float,
        "bool": bool,
        "tuple": tuple,
        "set": set,
        "type": type,
        "bytes": bytes,

        # Introspection (safe versions — block dunder access)
        "isinstance": isinstance,
        "hasattr": _safe_hasattr,
        "getattr": _safe_getattr,

        # Constants
        "None": None,
        "True": True,
        "False": False,

        # Math builtins
        "min": min,
        "max": max,
        "sum": sum,
        "abs": abs,
        "round": round,
        "any": any,
        "all": all,
        "divmod": divmod,
        "pow": pow,

        # Exceptions (required for try/except in scripts)
        "Exception": Exception,
        "ValueError": ValueError,
        "TypeError": TypeError,
        "KeyError": KeyError,
        "IndexError": IndexError,
        "RuntimeError": RuntimeError,
        "StopIteration": StopIteration,
        "AttributeError": AttributeError,
        "ZeroDivisionError": ZeroDivisionError,

        # String & formatting
        "repr": repr,
        "format": format,
        "chr": chr,
        "ord": ord,
    },
    # Allowed modules
    "gather": asyncio.gather,       # asyncio cherry-picked — no create_subprocess_*
    "sleep": asyncio.sleep,
    "wait_for": asyncio.wait_for,
    "json": json,
    "re": re,
    "math": math,
    "datetime": datetime,
}


# ---------------------------------------------------------------------------
# Phase 1 — Stub Registry
# ---------------------------------------------------------------------------
# Concurrency policy:
#   PARALLEL (no lock):     iterm2_read, iterm2_cwd
#   SERIAL (Semaphore(1)):  iterm2_write, iterm2_send_control,
#                           iterm2_send_text, osascript_run
#
# Serial tools acquire the semaphore before dispatching to the executor.
# This prevents macOS UI automation race conditions.
# ---------------------------------------------------------------------------

_SERIAL_LOCK = asyncio.Semaphore(1)
_STUBS: dict[str, Callable] | None = None


def _make_parallel_stub(sync_fn: Callable) -> Callable:
    """Wrap a sync tool as an async callable (no concurrency lock)."""
    async def stub(*args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, functools.partial(sync_fn, *args, **kwargs)
        )
    stub.__name__ = sync_fn.__name__.replace("impl_", "")
    stub.__doc__ = sync_fn.__doc__
    return stub


def _make_serial_stub(sync_fn: Callable) -> Callable:
    """Wrap a sync tool as an async callable with Semaphore(1) for serial access."""
    async def stub(*args, **kwargs):
        async with _SERIAL_LOCK:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, functools.partial(sync_fn, *args, **kwargs)
            )
    stub.__name__ = sync_fn.__name__.replace("impl_", "")
    stub.__doc__ = sync_fn.__doc__
    return stub


def build_stubs() -> dict[str, Callable]:
    """Build async-wrapped stubs for all composable MCP tools.

    Returns a cached singleton — stubs are stateless wrappers.
    """
    global _STUBS
    if _STUBS is not None:
        return _STUBS

    _STUBS = {
        # Parallel-safe (read-only)
        "iterm2_read":         _make_parallel_stub(impl_iterm2_read),
        "iterm2_cwd":          _make_parallel_stub(impl_iterm2_cwd),
        # Serial (UI mutation / macOS thread safety)
        "iterm2_write":        _make_serial_stub(impl_iterm2_write),
        "iterm2_send_control": _make_serial_stub(impl_iterm2_send_control),
        "iterm2_send_text":    _make_serial_stub(impl_iterm2_send_text),
        "osascript_run":       _make_serial_stub(impl_osascript_run),
    }
    return _STUBS


# ---------------------------------------------------------------------------
# Phase 2 — Sandboxed Executor
# ---------------------------------------------------------------------------

async def execute(code: str, stubs: dict) -> str:
    """Execute a compose script in a sandboxed namespace.

    Steps:
        1. AST validation — blocks dunder attribute access at parse time
        2. Build a fresh namespace from SANDBOX_GLOBALS + stubs
        3. Override print() to capture stdout to StringIO
        4. Wrap code in `async def __compose__():` before compile
        5. exec() to define __compose__, then await with timeout
        6. Return captured stdout + return value (capped at MAX_OUTPUT)
    """
    with log_tool_call("compose", payload_size=len(code)) as log:

        # 1. AST validation
        ast_error = _validate_ast(code)
        if ast_error:
            log["result_preview"] = ast_error
            return ast_error

        # 2. Build fresh namespace (shallow copy prevents cross-call mutation)
        namespace = {}
        for key, value in SANDBOX_GLOBALS.items():
            namespace[key] = dict(value) if key == "__builtins__" else value
        namespace.update(stubs)

        # 3. Capture stdout
        output_buffer = io.StringIO()

        def _captured_print(*args, **kwargs):
            kwargs.setdefault("file", output_buffer)
            print(*args, **kwargs)

        namespace["__builtins__"]["print"] = _captured_print

        # 4. Wrap in async def (bare `await` is invalid at module level)
        wrapped_code = "async def __compose__():\n" + textwrap.indent(code, "    ")

        # 5. Compile
        try:
            compiled = compile(wrapped_code, "<compose>", "exec")
        except SyntaxError as e:
            line = (e.lineno - 1) if e.lineno else "?"
            return f"❌ SyntaxError at line {line}: {e.msg}"

        # 6. Execute with timeout
        try:
            exec(compiled, namespace)
            compose_fn = namespace["__compose__"]
            return_value = await asyncio.wait_for(compose_fn(), timeout=EXECUTOR_TIMEOUT)
        except asyncio.TimeoutError:
            res = (
                f"⏱ Compose script timed out after {EXECUTOR_TIMEOUT}s.\n"
                f"Note: executor threads from run_in_executor may still be running."
            )
            log["result_preview"] = res[:200]
            return res
        except SyntaxError as e:
            line = (e.lineno - 1) if e.lineno else "?"
            res = f"❌ SyntaxError at line {line}: {e.msg}"
            log["result_preview"] = res[:200]
            return res
        except NameError as e:
            res = f"❌ NameError: {e}"
            log["result_preview"] = res[:200]
            return res
        except AttributeError as e:
            res = f"🚫 {e}"
            log["result_preview"] = res[:200]
            return res
        except Exception as e:
            tb = traceback.format_exc()
            res = f"❌ Compose error: {type(e).__name__}: {e}\n{tb}"
            log["result_preview"] = res[:200]
            return res

        # 7. Collect output
        result = output_buffer.getvalue()

        if return_value is not None:
            return_str = repr(return_value)
            if len(return_str) > 5000:
                return_str = return_str[:5000] + "... [truncated]"
            result += (f"\n\n→ Return value:\n{return_str}" if result else return_str)

        if len(result) > MAX_OUTPUT:
            result = result[:MAX_OUTPUT] + "\n⚠️ Output truncated at 50KB"

        final = result if result else "(compose script completed, no output)"
        log["result_preview"] = final[:200]
        return final


# ---------------------------------------------------------------------------
# Phase 3 — MCP Registration
# ---------------------------------------------------------------------------

def register(mcp: FastMCP) -> None:
    """Register the compose tool on the MCP server."""

    @mcp.tool()
    async def compose(code: str) -> str:
        """Execute a Python script that composes multiple MCP tool calls.

        All registered MCP tools are available as async functions inside
        the script. Use gather(...) for parallel calls.

        Available tools:
            iterm2_read(lines, tab)
            iterm2_write(text, wait, timeout, tab)
            iterm2_send_control(character, tab)
            iterm2_send_text(text, tab)
            iterm2_cwd(tab)
            osascript_run(script, timeout)

        Example (sequential):
            result = await iterm2_write("ls -la")
            print(result)

        Example (parallel):
            cwd, output = await gather(
                iterm2_cwd(),
                iterm2_read(lines=20)
            )
            print(f"CWD: {cwd}")
            print(output)

        Returns:
            Captured stdout and return value as a string.
        """
        if not COMPOSE_ENABLED:
            return "🚫 Compose is currently disabled. Use individual tools instead."
        stubs = build_stubs()
        return await execute(code, stubs)
