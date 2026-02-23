# Architecture

## Overview

The core of this repo is a single `compose(code: str)` MCP tool that lets an LLM write async Python to orchestrate multiple tool calls in one round-trip. Instead of the LLM calling `iterm2_read`, waiting, calling `iterm2_write`, waiting — it writes a script, sends it once, and gets back all results together.

This pattern was independently developed in parallel with Cloudflare's [Code Mode MCP](https://blog.cloudflare.com/code-mode-mcp/) (Feb 2026), which implements the same idea in JavaScript with V8 isolates for their cloud API.

---

## Three-Phase Design (`tools/codemode.py`)

### Phase 1 — Stub Registry

`build_stubs()` wraps each sync tool implementation (`impl_*`) as an async callable that dispatches to a thread pool via `loop.run_in_executor()`.

**Concurrency policy:**

| Policy | Tools |
|---|---|
| Parallel (no lock) | `iterm2_read`, `iterm2_cwd` |
| Serial (`Semaphore(1)`) | `iterm2_write`, `iterm2_send_control`, `iterm2_send_text`, `osascript_run` |

Read-only tools run freely in parallel. Mutation tools acquire a single semaphore before executing — this prevents macOS UI automation race conditions where two AppleScript `write text` calls can interleave and corrupt terminal state.

Stubs are a cached singleton. There is no state between compose script executions.

### Phase 2 — Sandboxed Executor

`execute(code, stubs)` runs a compose script safely:

1. **AST validation** — `_DunderChecker` walks the parse tree before compilation and raises on any `obj.__dunder__` attribute access. This blocks `(1).__class__.__mro__[1].__subclasses__()` style introspection chains at parse time.
2. **Fresh namespace** — `SANDBOX_GLOBALS` is shallow-copied per call. No state leaks between executions.
3. **Stdout capture** — `print()` is replaced with a `StringIO` writer. Output is capped at 50KB.
4. **Async wrapper** — code is wrapped in `async def __compose__():` before `compile()`, making bare `await` valid.
5. **Timeout** — `asyncio.wait_for(compose_fn(), timeout=30)` enforces a hard 30s limit.
6. **Return value capture** — if the script returns a value, it is appended to stdout as `→ Return value: ...`

### Phase 3 — MCP Registration

`register(mcp)` exposes the single `compose` tool on a `FastMCP` server. A `COMPOSE_ENABLED` flag allows kill-switching the tool without restarting the server.

---

## Sandbox Globals

The whitelist namespace injected into every compose script:

```
Built-ins:   print, len, range, enumerate, zip, map, filter, sorted, reversed,
             list, dict, str, int, float, bool, tuple, set, type, bytes,
             isinstance, hasattr*, getattr*, None, True, False,
             min, max, sum, abs, round, any, all, divmod, pow,
             Exception + common subclasses, repr, format, chr, ord

Modules:     json, re, math, datetime

asyncio:     gather, sleep, wait_for  (cherry-picked — no create_subprocess_*)

Tools:       iterm2_read, iterm2_cwd, iterm2_write, iterm2_send_control,
             iterm2_send_text, osascript_run
```

`*` — `hasattr` and `getattr` are replaced with safe versions that block dunder access at runtime (belt-and-suspenders alongside the AST check).

**Notably absent:** `os`, `sys`, `subprocess`, `importlib`, `open`, `eval`, `exec`, `compile`, `__import__`.

---

## Sandbox Security Model

The sandbox is a **guardrail, not a security boundary.**

It is designed to catch:
- Accidental `os.system()` calls from LLM hallucinations
- Runaway loops (timeout)
- Dunder introspection chains (`__class__.__mro__` etc.)
- Stdout floods (50KB cap)

It is **not** designed to defend against:
- Intentional exploitation by a motivated attacker
- Prompt injection from untrusted terminal content that gets fed back into a compose script

The real security model is: **trusted local agent, trusted user.** This is a local macOS tool running under your own account. If you need to accept compose scripts from untrusted sources, the right solution is OS-level isolation: run scripts in a subprocess with `seccomp`, a container, or a real VM.

---

## impl/register Pattern

All tool modules follow a consistent two-function pattern:

```python
# Module-level — called directly by compose stubs
def impl_iterm2_write(text, wait=True, timeout=8, tab=None) -> str:
    ...

# MCP registration — wraps impl as a named tool
def register(mcp: FastMCP) -> None:
    @mcp.tool()
    def iterm2_write(text, wait=True, timeout=8, tab=None) -> str:
        return impl_iterm2_write(text, wait=wait, timeout=timeout, tab=tab)
```

This separation means:
- Compose stubs call `impl_*` directly (sync, no MCP overhead)
- The MCP server exposes the same logic as named tools
- Adding a new tool requires no changes to `codemode.py` — just add `impl_*` and a stub entry in `build_stubs()`

---

## Structured Logging

Every tool call is logged via `utils/logger.py`'s `log_tool_call()` context manager:

```python
with log_tool_call("iterm2_write", text=text) as log:
    result = do_thing()
    log["result_preview"] = result[:200]
```

Output: `logs/mcp/tools.jsonl` — one JSON object per line, easy to `tail -f | jq .`

Fields: `tool`, `params`, `timestamp`, `status`, `duration_ms`, `result_preview`, `error` (if any).

---

## Cloudflare Convergence

Cloudflare's Code Mode and this repo arrived at the same architecture, the port wouldnt have been possible without cloudflares initial work

| Cloudflare Code Mode | python-codemode-mcp |
|---|---|
| `search()` + `execute()` | `compose(code: str)` |
| V8 isolate (dynamic, per-execution) | `SANDBOX_GLOBALS` + AST guard + fresh namespace |
| `cloudflare.request()` injected | `iterm2_write()`, `osascript_run()` injected |
| Outbound fetch disabled by default | `Semaphore(1)` serialises mutation tools |
| JavaScript | Python |
| Cloud API (2,500 endpoints) | Local macOS agent (terminal + AppleScript) |

The key structural insight is the same: **give the model a scripting environment with injected capabilities, not a list of individual tools.** Token cost drops dramatically, round-trips collapse, and the model can express conditional logic and parallelism naturally.
