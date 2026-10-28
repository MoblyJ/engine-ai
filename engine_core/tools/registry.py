"""Tool registry (upgrade.md §4).

A profile's `read_only` flag and its permitted tools are enforced HERE, by the executor,
not merely included in the prompt. `default_registry()` exposes the existing MCP tools
(compatibility surface) when the mcp package is importable.
"""

from __future__ import annotations

import os

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


def _register_build_tools(reg: Registry) -> None:
    """File + shell tools workers need to actually BUILD things.

    Path arguments are contained to the workspace by Policy (executor-side); these
    handlers additionally default to the context workspace and bound every result.
    """

    def _root(args: dict, context: dict | None) -> str:
        ws = (context or {}).get("workspace") or os.getcwd()
        return args.get("cwd") or args.get("root") or ws

    def read_file(args, context=None):
        path = os.path.join(_root(args, context), args["path"])
        with open(path, encoding="utf-8", errors="ignore") as f:
            return {"ok": True, "path": args["path"], "content": f.read(50_000)}

    def write_file(args, context=None):
        path = os.path.join(_root(args, context), args["path"])
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        data = args.get("content", "")
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)
        return {"ok": True, "path": args["path"], "bytes": len(data)}

    def list_dir(args, context=None):
        base = os.path.join(_root(args, context), args.get("path", ""))
        entries = []
        for name in sorted(os.listdir(base))[:200]:
            full = os.path.join(base, name)
            entries.append(("/" if os.path.isdir(full) else " ", name))
        return {"ok": True, "path": args.get("path", "."), "entries": entries}

    def run_command(args, context=None):
        import subprocess
        argv = args["command"] if isinstance(args["command"], list) else \
            ["bash", "-c", str(args["command"])]
        try:
            p = subprocess.run(argv, cwd=_root(args, context), capture_output=True,
                               text=True, timeout=int(args.get("timeout", 60)),
                               stdin=subprocess.DEVNULL)
            return {"ok": p.returncode == 0, "exit_code": p.returncode,
                    "stdout": (p.stdout or "")[-4000:], "stderr": (p.stderr or "")[-2000:]}
        except subprocess.TimeoutExpired:
            return {"ok": False, "exit_code": None, "stderr": "command timed out"}
        except OSError as e:
            return {"ok": False, "exit_code": None, "stderr": f"{e.__class__.__name__}: {e}"}

    reg.register("read_file", read_file,
                 {"type": "object", "properties": {"path": {"type": "string"}},
                  "required": ["path"]}, read_only=True,
                 description="Read a file in the workspace (bounded to 50k chars).")
    reg.register("write_file", write_file,
                 {"type": "object", "properties": {"path": {"type": "string"},
                                                   "content": {"type": "string"}},
                  "required": ["path", "content"]},
                 description="Create/overwrite a file in the workspace.")
    reg.register("list_dir", list_dir,
                 {"type": "object", "properties": {"path": {"type": "string"}}},
                 read_only=True, description="List a workspace directory.")
    reg.register("run_command", run_command,
                 {"type": "object",
                  "properties": {"command": {}, "timeout": {"type": "integer"}},
                  "required": ["command"]},
                 description="Run a command in the workspace (bounded, no stdin).")


def build_registry() -> Registry:
    """The tools a build worker needs (no MCP surface — standalone-safe)."""
    reg = Registry()
    reg.register("report_status", lambda a: {"ok": True, "status": "working"},
                 {"type": "object", "properties": {}}, read_only=True,
                 description="Report progress")
    _register_build_tools(reg)
    return reg


def default_registry(mcp_tools: bool = True) -> Registry:
    """A registry with the MCP toolkit's tools registered (read-only ones marked)."""
    reg = build_registry()
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
