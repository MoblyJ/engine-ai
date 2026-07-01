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
import experts as _experts  # noqa: E402
from knowledge import Knowledge  # noqa: E402
from memory import Memory  # noqa: E402
from sessions import Sessions  # noqa: E402

PROTOCOL = "2024-11-05"
ENGINE = Engine()
MEM = Memory()
SESS = Sessions()
KN = Knowledge()

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


@tool("memory_save", "Save a memory pocket: KEYWORDS defining the context + the context text + optional structured data. If it's similar to an existing pocket it EVOLVES that one (merges keywords+memory+data). Call after building so future prompts build on it.",
      _obj({"keywords": {"type": "array", "items": {"type": "string"}}, "context": {"type": "string"},
            "data": {"type": "object"}, "category": {"type": "string"}}, ["keywords", "context"]))
def _memory_save(a):
    return MEM.save(a["keywords"], a["context"], a.get("data"), a.get("category"))


@tool("memory_recall", "Recall the closest memory pockets by KEYWORDS (hybrid keyword+embedding). If two or more pockets share keywords, returns the closest ones with BOTH memory and data merged — the evolving context to build the next app on.",
      _obj({"keywords": {"type": "array", "items": {"type": "string"}}, "k": {"type": "integer"}}, ["keywords"]))
def _memory_recall(a):
    return MEM.recall(a["keywords"], k=int(a.get("k", 3)))


@tool("memory_context", "Return a single ready-to-inject context string built from the closest memory pockets for these keywords — feed it into a build prompt so the app EVOLVES from prior work.",
      _obj({"keywords": {"type": "array", "items": {"type": "string"}}}, ["keywords"]))
def _memory_context(a):
    return {"context": MEM.context(a["keywords"])}


@tool("memory_list", "List all memory pockets (keywords, category, hits, preview).", _obj({}))
def _memory_list(a):
    return {"pockets": MEM.list()}


@tool("memory_forget", "Delete a memory pocket by id.", _obj({"id": {"type": "integer"}}, ["id"]))
def _memory_forget(a):
    return MEM.forget(int(a["id"]))


@tool("app_create", "Start a new isolated app session in its own GIT WORKTREE (own branch + folder). Returns the worktree path to build in. Call this at the start of /new-app.",
      _obj({"name": {"type": "string"}, "keywords": {"type": "array", "items": {"type": "string"}},
            "path": {"type": "string"}}, ["name"]))
def _app_create(a):
    return SESS.create(a["name"], keywords=a.get("keywords"), path=a.get("path"))


@tool("app_update", "Save a 2-line summary + keywords for an app session (call after building so /resume-app shows what it is).",
      _obj({"id": {"type": "integer"}, "summary": {"type": "string"}, "keywords": {"type": "array", "items": {"type": "string"}}}, ["id"]))
def _app_update(a):
    return SESS.update(int(a["id"]), summary=a.get("summary"), keywords=a.get("keywords"))


@tool("app_list", "List all previous app sessions (id, name, path, branch, and a 2-line summary) for /resume-app.", _obj({}))
def _app_list(a):
    return {"sessions": SESS.list()}


@tool("app_resume", "Resume a previous app by id or slug — returns its worktree path, branch, and recalled memory context to continue where it left off.",
      _obj({"key": {"type": "string"}}, ["key"]))
def _app_resume(a):
    return SESS.resume(a["key"])


@tool("app_find", "Identify which app session a working directory belongs to (its worktree/branch). Use in /ship-live so shipping targets the right app.",
      _obj({"path": {"type": "string"}}, ["path"]))
def _app_find(a):
    return SESS.find(a["path"])


@tool("knowledge_ingest", "Clone/ingest an engineering repo (URL or local path) into the domain-tagged knowledge store so domain agents can retrieve it. This is how agents 'master' a domain — by retrieval, not training.",
      _obj({"source": {"type": "string"}, "domain": {"type": "string"}, "repo": {"type": "string"}}, ["source", "domain"]))
def _knowledge_ingest(a):
    return KN.ingest_repo(a["source"], a["domain"], a.get("repo"))


@tool("knowledge_search", "Search the ingested engineering knowledge (roadmaps, system design, ML, LLM, build-your-own-x, …), optionally filtered by domain. Returns the most relevant chunks with source.",
      _obj({"query": {"type": "string"}, "domain": {"type": "string"}, "k": {"type": "integer"}}, ["query"]))
def _knowledge_search(a):
    return {"hits": KN.search(a["query"], domain=a.get("domain"), k=int(a.get("k", 6)))}


@tool("knowledge_domains", "List the knowledge domains available (and their chunk/repo counts) + ingested sources.", _obj({}))
def _knowledge_domains(a):
    return {"domains": KN.domains(), "sources": KN.sources()}


@tool("context_pack", "Assemble the FULL context for a task: prior MEMORY (what we've built) + DOMAIN KNOWLEDGE (retrieved expertise) → one blob to feed a build or answer. The 'perfect context' entrypoint.",
      _obj({"keywords": {"type": "array", "items": {"type": "string"}}, "domain": {"type": "string"}}, ["keywords"]))
def _context_pack(a):
    return {"context": KN.context_pack(a["keywords"], domain=a.get("domain"))}


@tool("suggest_experts", "Deterministically route a request to the domain expert(s) to consult (ranked domain-<slug> agents + whether each has ingested knowledge). Use in /new-app and /expert for repeatable routing.",
      _obj({"request": {"type": "string"}, "k": {"type": "integer"}}, ["request"]))
def _suggest_experts(a):
    return _experts.suggest(a["request"], k=int(a.get("k", 3)), knowledge=KN)


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
