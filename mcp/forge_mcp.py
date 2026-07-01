#!/usr/bin/env python3
"""engine-ai MCP server (stdio, JSON-RPC 2.0) for Claude Code.

Claude Code launches this via `claude mcp add`. It speaks the Model Context Protocol over
stdin/stdout (newline-delimited JSON) and exposes the toolkit as callable tools:

  index_repo, search_repo            — repo-aware RAG grounding
  list_skills, get_skill, import_repo_skills — the unified skill registry
  set_secret, list_secrets           — encrypted secrets vault
  deploy_readiness                   — what a project still needs to be deployable
  scaffold_app                       — write a deployable skeleton (node-api/python-api/static)

Pure standard library — no pip install required.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deploy import deploy_readiness, scaffold  # noqa: E402
from engine import Engine  # noqa: E402
from integrations import git_publish, responsive_audit, vercel_deploy  # noqa: E402

PROTOCOL = "2024-11-05"
ENGINE = Engine()

# ----------------------------------------------------------- tool registry
TOOLS: list[dict] = []
HANDLERS: dict = {}


def tool(name, description, schema):
    def deco(fn):
        TOOLS.append({"name": name, "description": description, "inputSchema": schema})
        HANDLERS[name] = fn
        return fn
    return deco


def _obj(props, required=None):
    return {"type": "object", "properties": props, "required": required or []}


@tool("index_repo", "Build/refresh the searchable index of a repository so answers are grounded in its code.",
      _obj({"path": {"type": "string", "description": "absolute path to the repo"}}, ["path"]))
def _index_repo(a):
    return ENGINE.index_repo(os.path.abspath(a["path"]))


@tool("search_repo", "Semantic-ish search over an indexed repo. Returns the most relevant code/doc chunks.",
      _obj({"path": {"type": "string"}, "query": {"type": "string"}, "k": {"type": "integer"}}, ["path", "query"]))
def _search_repo(a):
    return ENGINE.search_repo(os.path.abspath(a["path"]), a["query"], k=int(a.get("k", 5)))


@tool("list_skills", "List/search the unified skill registry (workflows imported from agent-skills, gstack, oh-my-pi, and bundled).",
      _obj({"query": {"type": "string"}}))
def _list_skills(a):
    return ENGINE.list_skills(a.get("query", ""))


@tool("get_skill", "Get the full body of one skill by name (the workflow to follow).",
      _obj({"name": {"type": "string"}}, ["name"]))
def _get_skill(a):
    return ENGINE.get_skill(a["name"]) or {"error": "not found"}


@tool("import_repo_skills", "Import all SKILL.md skills from a repo (auto-detects agent-skills / gstack / .omp layouts).",
      _obj({"path": {"type": "string"}}, ["path"]))
def _import_repo_skills(a):
    return {"imported": ENGINE.import_repo_skills(os.path.abspath(a["path"]))}


@tool("set_secret", "Store a secret (encrypted at rest). Use for API keys/tokens the app needs at deploy time.",
      _obj({"name": {"type": "string"}, "value": {"type": "string"}, "scope": {"type": "string"}}, ["name", "value"]))
def _set_secret(a):
    ENGINE.set_secret(a["name"], a["value"], a.get("scope", "global"))
    return {"ok": True, "name": a["name"]}


@tool("list_secrets", "List secret NAMES (never values) in a scope.",
      _obj({"scope": {"type": "string"}}))
def _list_secrets(a):
    return {"names": ENGINE.list_secrets(a.get("scope", "global"))}


@tool("deploy_readiness", "Scan a project and return a deployability checklist + recommendations (stack, tests, Dockerfile, CI, env, README).",
      _obj({"path": {"type": "string"}}, ["path"]))
def _deploy_readiness(a):
    return deploy_readiness(os.path.abspath(a["path"]))


@tool("scaffold_app", "Write a minimal but genuinely deployable app skeleton (healthcheck + tests + Dockerfile + CI). kind: node-api | python-api | static.",
      _obj({"path": {"type": "string"}, "kind": {"type": "string"}}, ["path", "kind"]))
def _scaffold_app(a):
    return scaffold(os.path.abspath(a["path"]), a["kind"])


@tool("git_publish", "Create a GitHub repo from a folder and push it. Requires `gh auth login` first; returns needs_auth with the exact command if not logged in. Ask the user for the repo name before calling.",
      _obj({"path": {"type": "string"}, "repo_name": {"type": "string"}, "private": {"type": "boolean"}}, ["path", "repo_name"]))
def _git_publish(a):
    return git_publish(os.path.abspath(a["path"]), a["repo_name"], private=a.get("private", True))


@tool("vercel_deploy", "Deploy a project to Vercel and return the live URL. Requires `vercel login` or VERCEL_TOKEN; returns needs_auth otherwise. Best for static/Next/frontend apps. Set prod=true for production.",
      _obj({"path": {"type": "string"}, "prod": {"type": "boolean"}}, ["path"]))
def _vercel_deploy(a):
    return vercel_deploy(os.path.abspath(a["path"]), prod=a.get("prod", False))


@tool("responsive_audit", "Static mobile-responsiveness audit of a frontend (viewport meta, media queries, responsive units, flex/grid, responsive images). Returns a score + findings.",
      _obj({"path": {"type": "string"}}, ["path"]))
def _responsive_audit(a):
    return responsive_audit(os.path.abspath(a["path"]))


# ----------------------------------------------------------- JSON-RPC plumbing
def _result(id_, result):
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _error(id_, code, message):
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def handle(msg: dict):
    method = msg.get("method")
    id_ = msg.get("id")
    if method == "initialize":
        proto = (msg.get("params") or {}).get("protocolVersion", PROTOCOL)
        return _result(id_, {
            "protocolVersion": proto,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "engine-ai", "version": "0.1.0"},
        })
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None  # notifications: no response
    if method == "ping":
        return _result(id_, {})
    if method == "tools/list":
        return _result(id_, {"tools": TOOLS})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        fn = HANDLERS.get(name)
        if not fn:
            return _error(id_, -32601, f"unknown tool: {name}")
        try:
            out = fn(args)
            return _result(id_, {"content": [{"type": "text", "text": json.dumps(out, indent=2)}]})
        except Exception as e:  # noqa: BLE001
            return _result(id_, {"content": [{"type": "text", "text": f"error: {e!r}"}], "isError": True})
    if id_ is not None:
        return _error(id_, -32601, f"method not found: {method}")
    return None


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
