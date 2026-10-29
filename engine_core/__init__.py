"""engine_core — the provider-independent coding runtime (v0.16, per the upgrade review).

Layers (each importable standalone, pure stdlib):
- providers/ : model catalog + OpenRouter/local adapters, rate limiting per account group
- runtime/   : task dependency graph, lease-based state store, scheduler, worker loop
- context/   : budgeted context packets, symbol spans, retrieval sequence
- memory/    : project-scoped memory facade over the mcp store
- workspaces/: git task worktrees + the single serialized integrator
- tools/     : registry, policy-enforcing executor, effect journal
- verification/: command runner + evidence manifests
- telemetry/ : usage/cost journal + append-only event log

The MCP tool names in mcp/forge_mcp.py stay the stable public surface; engine_core is the
extracted runtime that will gradually absorb them.
"""

__version__ = "0.17.6"
