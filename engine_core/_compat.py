"""Bridge to the mcp/ modules (kept importable for the transition; see upgrade.md §2)."""

from __future__ import annotations

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP_DIR = os.path.join(REPO_ROOT, "mcp")


def mcp_module(name: str):
    """Import a module from mcp/ (adding its dir to sys.path once)."""
    if MCP_DIR not in sys.path:
        sys.path.insert(0, MCP_DIR)
    return __import__(name)
