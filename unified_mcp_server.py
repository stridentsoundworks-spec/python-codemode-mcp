#!/usr/bin/env python3
"""Unified MCP Server — Code Mode.

Exposes a single `compose` tool that lets an LLM write async Python
to orchestrate multiple tool calls in one round-trip.

Usage:
    python unified_mcp_server.py

MCP client config (Claude Desktop / Perplexity / etc.):
    {
      "mcpServers": {
        "codemode": {
          "command": "/path/to/.venv/bin/python3",
          "args": ["/path/to/python-codemode-mcp/unified_mcp_server.py"]
        }
      }
    }
"""

import sys
from pathlib import Path

# Editable install via `pip install -e .` is preferred.
# This fallback supports running directly from the repo root without installing.
sys.path.insert(0, str(Path(__file__).parent))

from mcp.server.fastmcp import FastMCP
from tools import codemode

server = FastMCP("codemode")
codemode.register(server)

if __name__ == "__main__":
    server.run(transport="stdio")
