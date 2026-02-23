# python-codemode-mcp

> A Python adaptation of the [Code Mode MCP pattern](https://blog.cloudflare.com/code-mode-mcp/) for local agent stacks — built for macOS + iTerm2.

## The Problem

An LLM controlling a terminal through individual MCP tools looks like this:

```
LLM → iterm2_cwd()          → wait → result
LLM → iterm2_read(20)       → wait → result
LLM → iterm2_write("ls")    → wait → result
```

Three round-trips. Three waits. Three context window additions. And the LLM can't parallelise or branch based on intermediate results without another round-trip.

## The Solution

Expose a single `compose` tool. The LLM writes an async Python script that orchestrates everything in **one round-trip**:

```python
# One compose() call does all of this:
cwd, snapshot = await gather(
    iterm2_cwd(),
    iterm2_read(lines=20)
)

if "my-project" in cwd:
    result = await iterm2_write("git status")
    print(result)
else:
    print(f"Wrong directory: {cwd}")
```

This is the same insight Cloudflare published in their [Code Mode MCP post](https://blog.cloudflare.com/code-mode-mcp/) (Feb 2026) — built in Python for a local macOS agent stack rather than JavaScript for a cloud API.

---

## Architecture

Three phases inside `tools/codemode.py`:

| Phase | What it does |
|---|---|
| **1. Stub Registry** | Wraps sync `impl_*` functions as async callables; read-only tools run in parallel, mutation tools serialised via `Semaphore(1)` to prevent macOS UI race conditions |
| **2. Sandboxed Executor** | AST dunder check → fresh namespace from `SANDBOX_GLOBALS` → stdout capture → `exec()` → `asyncio.wait_for()` with 30s timeout |
| **3. MCP Registration** | Single `compose(code: str)` tool on a `FastMCP` server, with `COMPOSE_ENABLED` kill-switch |

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design.

### Cloudflare ↔ python-codemode-mcp

| Cloudflare Code Mode | This repo |
|---|---|
| `search()` + `execute()` | `compose(code: str)` |
| V8 isolate (per-execution) | `SANDBOX_GLOBALS` + AST guard + fresh namespace |
| `cloudflare.request()` injected | `iterm2_write()`, `osascript_run()` injected |
| Outbound fetch disabled by default | `Semaphore(1)` serialises mutation tools |
| Dynamic Worker isolate | Per-call fresh namespace copy |

---

## Sandbox Security Model

The sandbox is a **guardrail, not a security boundary.** It catches:
- Accidental `os.system()` / `subprocess` calls from LLM hallucinations
- Dunder introspection chains (`__class__.__mro__[1].__subclasses__()`) — blocked at AST parse time
- Runaway scripts — 30s hard timeout
- Stdout floods — 50KB cap

What it does **not** defend against: intentional exploitation or prompt injection from untrusted sources. The security model assumes a trusted local agent running under your own account. For untrusted input, use OS-level isolation (subprocess sandbox, seccomp, container).

---

## Prerequisites

- macOS (iTerm2 tools use AppleScript automation)
- [iTerm2](https://iterm2.com) with [Shell Integration](https://iterm2.com/documentation-shell-integration.html) installed (recommended for `iterm2_cwd`)
- Python 3.11+

---

## Installation

```bash
git clone https://github.com/stridentsoundworks-spec/python-codemode-mcp
cd python-codemode-mcp
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### MCP Client Config

Add to your MCP client (Claude Desktop, Perplexity, etc.):

```json
{
  "mcpServers": {
    "codemode": {
      "command": "/path/to/python-codemode-mcp/.venv/bin/python3",
      "args": ["/path/to/python-codemode-mcp/unified_mcp_server.py"]
    }
  }
}
```

---

## Writing Compose Scripts

All tool functions are injected into the script namespace as async callables. See [`docs/LLM_INSTRUCTIONS.md`](docs/LLM_INSTRUCTIONS.md) for the full guide — this is the exact prompt context provided to the LLM.

### Available functions

| Function | Description |
|---|---|
| `iterm2_read(lines, tab)` | Read last N lines from terminal |
| `iterm2_write(text, wait, timeout, tab)` | Send command, wait for output |
| `iterm2_send_control(character, tab)` | Send Ctrl-C, Ctrl-D, etc. |
| `iterm2_send_text(text, tab)` | Send text without Enter |
| `iterm2_cwd(tab)` | Get current working directory |
| `osascript_run(script, timeout)` | Execute raw AppleScript |

### Concurrency

Use `gather()` (pre-injected from `asyncio`) for parallel calls:

```python
# Read-only tools run in parallel
cwd, output = await gather(iterm2_cwd(), iterm2_read(lines=30))
```

Mutation tools (`iterm2_write`, `osascript_run`, etc.) are automatically serialised — you don’t need to manage this.

### Return values

Scripts can return values directly:

```python
cwd = await iterm2_cwd()
return cwd  # appears as → Return value: '/path/to/dir'
```

---

## Skills

The `skills/` directory holds reusable compose scripts. Each skill exposes a `SCRIPT` string and a `DESCRIPTION`. Drop the script into a `compose()` call:

```python
# skills/read-and-speak.py
# Reads terminal state and returns a speakable summary
```

The skills pattern keeps common orchestration logic version-controlled and reusable across sessions.

---

## Logs

Every tool call is logged to `logs/mcp/tools.jsonl`:

```bash
tail -f logs/mcp/tools.jsonl | jq .
```

Fields: `tool`, `params`, `timestamp`, `status`, `duration_ms`, `result_preview`.

---

## Adding a New Tool

1. Add `impl_your_tool()` to the appropriate module in `tools/`
2. Add a stub entry to `build_stubs()` in `tools/codemode.py`
3. Add `impl_your_tool` to the `register()` call in `unified_mcp_server.py`
4. Document it in `docs/LLM_INSTRUCTIONS.md`

No other changes required.

---

## License

MIT
