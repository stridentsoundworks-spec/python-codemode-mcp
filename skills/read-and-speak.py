"""Skill: read-and-speak

Reads the current terminal output and produces a speakable summary.
Demonstrates the compose + individual tool cooperation pattern.

Usage:
    Paste SCRIPT into a compose() call. The returned summary can then
    be passed to a TTS tool as a separate call.

Pattern:
    Use compose for data gathering + transformation.
    Use direct tools for side effects that benefit from LLM judgment.
    This keeps skills reusable and the LLM in control of output routing.
"""

SCRIPT = """
output = await iterm2_read(lines=20)
lines = output.strip().splitlines()
cwd = await iterm2_cwd()

non_empty = [l for l in lines if l.strip()]
last_meaningful = non_empty[-1] if non_empty else "(empty)"

summary = []
summary.append(f"Directory: {cwd}")
summary.append(f"Terminal has {len(lines)} visible lines")
summary.append(f"Last activity: {last_meaningful}")

print("\\n".join(summary))
""".strip()

DESCRIPTION = "Read terminal state and produce a speakable summary."
