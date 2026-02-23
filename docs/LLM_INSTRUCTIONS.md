# MCP Instructions

You are equipped with the **CodeMode Compose Sandbox**, allowing you to execute complex, multi-step actions on the host machine using a single tool call: `compose(code: str)`.

Instead of making slow, sequential tool calls, you must write asynchronous Python scripts to orchestrate your actions.

## 🛠 Available Sandbox Functions

When writing your `compose` scripts, the following async functions are pre-injected into your global namespace. **Always `await` them**.

- `iterm2_read(lines: int = 50, tab: int | None = None) -> str`
  Reads recent output from an iTerm2 session.
- `iterm2_write(text: str, wait: bool = True, timeout: int = 8, tab: int | None = None) -> str`
  Writes text or a command to an iTerm2 session and waits for the output to settle.
- `iterm2_send_control(character: str, tab: int | None = None) -> str`
  Sends a control character (e.g., `'C'` for Ctrl-C, `'D'` for Ctrl-D).
- `iterm2_send_text(text: str, tab: int | None = None) -> str`
  Sends text WITHOUT pressing Enter (useful for interactive prompts: y/n, passwords).
- `iterm2_cwd(tab: int | None = None) -> str`
  Returns the absolute path of the current working directory.
- `osascript_run(script: str, timeout: int = 10) -> str`
  Executes raw AppleScript code.

## ⚡ Concurrency & Built-ins

- You have access to standard Python built-ins (`print`, `len`, `range`, `dict`, `list`, etc.) and modules (`json`, `re`, `math`, `datetime`).
- For concurrency, use the pre-injected `gather(...)` and `sleep(...)` (cherry-picked from `asyncio`). Do **not** use `asyncio.gather` — just `gather`.

## ⚠️ Sandbox Restrictions

1. **Dunder attributes blocked:** You cannot use `__dict__`, `__class__`, or any dunder attributes. Attempting to do so will raise a `SyntaxError` at parse time.
2. **No imports:** `import` statements are not available. All necessary modules are pre-injected.
3. **Timeout:** Scripts have a hard 30-second execution limit.
4. **Output limit:** Print output is capped at 50,000 characters.

## 📝 Scripting Guidelines

- **Print to return:** Use `print()` to output information you need to read back. Both stdout and the script’s raw return value are captured.
- **Fail gracefully:** Use `try/except` blocks if a step might fail but you still want to continue.
- **Return values work:** You can `return` a value from the script and it will be appended to the output as `→ Return value: ...`
- **Parallel reads:** Use `gather()` for concurrent read-only calls. Mutation tools (`iterm2_write`, `osascript_run`, etc.) are automatically serialised.

## 💡 Examples

### Example 1: Check Directory and Run a Command
```python
cwd = await iterm2_cwd()
print(f"Current Directory: {cwd}")

if "my_project" in cwd:
    output = await iterm2_write("git status")
    print(output)
else:
    print("Not in the correct directory.")
```

### Example 2: Parallel Reads
```python
# Fetch CWD and recent terminal output simultaneously
cwd, output = await gather(
    iterm2_cwd(),
    iterm2_read(lines=20)
)
print(f"CWD: {cwd}")
print("--- Recent Output ---")
print(output)
```

### Example 3: Multi-step with Error Handling
```python
try:
    result = await iterm2_write("npm test", timeout=30)
    if "PASS" in result:
        print("All tests passed.")
    else:
        print(f"Tests may have failed:\n{result}")
except Exception as e:
    print(f"Error running tests: {e}")
```

### Example 4: Interactive Prompt
```python
# Send a command that prompts for confirmation
await iterm2_write("npm publish", wait=False)
await sleep(1)
await iterm2_send_text("y")  # answer the y/n prompt without Enter
```

### Example 5: Return a Value
```python
cwd, output = await gather(iterm2_cwd(), iterm2_read(lines=5))
return {"cwd": cwd, "last_lines": output.strip().splitlines()}
# Result appears as: → Return value: {'cwd': '...', 'last_lines': [...]}
```
