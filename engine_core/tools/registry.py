"""Tool registry (upgrade.md §4).

A profile's `read_only` flag and its permitted tools are enforced HERE, by the executor,
not merely included in the prompt. `default_registry()` exposes the existing MCP tools
(compatibility surface) when the mcp package is importable.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ToolEntry:
    fn: object
    schema: dict
    read_only: bool = False
    description: str = ""


class UnknownTool(KeyError):
    pass


class Registry:
    def __init__(self):
        self._tools: dict[str, ToolEntry] = {}

    def register(self, name: str, fn, schema: dict | None = None, read_only: bool = False,
                 description: str = "") -> None:
        self._tools[name] = ToolEntry(fn=fn, schema=schema or {}, read_only=read_only,
                                      description=description)

    def get(self, name: str) -> ToolEntry:
        if name not in self._tools:
            raise UnknownTool(name)
        return self._tools[name]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self) -> dict[str, dict]:
        return {name: {"inputSchema": t.schema, "description": t.description or name,
                       "read_only": t.read_only} for name, t in self._tools.items()}


def default_registry(mcp_tools: bool = True) -> Registry:
    """A registry with the MCP toolkit's tools registered (read-only ones marked)."""
    reg = Registry()
    reg.register("report_status", lambda a: {"ok": True, "status": "working"},
                 {"type": "object", "properties": {}}, read_only=True, description="Report progress")
    if mcp_tools:
        try:
            from engine_core._compat import mcp_module
            forge = mcp_module("forge_mcp")
            forge.TOOLS  # force registry population without running main()
            read_only_tools = {"search_repo", "list_skills", "get_skill", "list_skills",
                               "list_secrets", "deploy_readiness", "memory_recall", "memory_get",
                               "memory_list", "memory_context", "app_list", "app_find",
                               "knowledge_search", "knowledge_domains", "context_pack",
                               "suggest_experts", "web_search", "web_search_status",
                               "so_search", "so_debug", "responsive_audit", "index_repo",
                               "import_repo_skills", "app_resume"}
            for spec in forge.TOOLS:
                reg.register(spec["name"], forge.HANDLERS[spec["name"]],
                             schema=spec.get("inputSchema"),
                             read_only=spec["name"] in read_only_tools,
                             description=spec.get("description", ""))
        except Exception:  # noqa: BLE001 - mcp surface optional for the standalone runtime
            pass
    return reg
