#!/usr/bin/env python3
"""engine-ai MCP server (stdio, JSON-RPC 2.0) for Claude Code.

Claude Code launches this via `claude mcp add`. It speaks the Model Context Protocol over
stdin/stdout (newline-delimited JSON) and exposes the toolkit as callable tools:

  index_repo, search_repo            — repo-aware RAG grounding (incremental, with line spans)
  list_skills, get_skill, import_repo_skills — the unified skill registry
  set_secret, list_secrets           — encrypted secrets vault (AES-256-GCM)
  deploy_readiness, deploy_verify, scaffold_app — inventory vs. executed evidence; safe scaffolding
  web_search, web_search_status      — live web search (current info beyond the offline knowledge store)
  so_search, so_debug                — StackOverflow Q&A search + error-message debugging, cited
  memory_*                           — project-scoped, revision-tracked memory pockets
  app_*                              — git-worktree app sessions
  knowledge_*, context_pack          — domain knowledge store + budgeted context compilation
  suggest_experts                    — deterministic expert routing with typed task profiles

v0.16 protocol notes (per the upgrade review):
- **Operational vs. protocol errors are distinct**: a tool returning `{"ok": false, ...}`
  is a normal result (serialized, `isError` unset); only exceptions inside a handler become
  `isError: true` tool results; malformed requests get JSON-RPC error codes
  (-32700 parse, -32600 invalid request, -32601 method not found, -32602 invalid params).
- **Malformed input is answered, never discarded**: bad JSON lines get a -32700 response.
- **Version negotiation**: initialize echoes the client's protocolVersion only when the
  server supports it; otherwise it responds with its newest supported version.

Pure standard library — no pip install required.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root → engine_core
from deploy import deploy_readiness, scaffold, verify  # noqa: E402
from engine import Engine, HOME, vault_backend  # noqa: E402
from integrations import git_publish, responsive_audit, vercel_deploy  # noqa: E402
import experts as _experts  # noqa: E402
from knowledge import Knowledge  # noqa: E402
from memory import Memory  # noqa: E402
from sessions import Sessions  # noqa: E402
from stackoverflow import StackOverflowClient  # noqa: E402
from websearch import WebSearch  # noqa: E402

PROTOCOLS = ["2024-11-05", "2025-03-26", "2025-06-18"]  # supported, oldest → newest

def _version() -> str:
    try:
        pkg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "package.json")
        with open(pkg, encoding="utf-8") as f:
            return json.load(f).get("version", "0.0.0")
    except (OSError, json.JSONDecodeError):
        return "0.0.0"

ENGINE = Engine()
MEM = Memory()
SESS = Sessions()
KN = Knowledge()
WS = WebSearch(get_secret=ENGINE.get_secret)
SO = StackOverflowClient(get_secret=ENGINE.get_secret)

# ----------------------------------------------------------- tool registry
TOOLS: list[dict] = []
HANDLERS: dict = {}


def tool(name, description, schema):
    def deco(fn):
        TOOLS.append({"name": name, "description": description, "inputSchema": schema})
        HANDLERS[name] = fn
        return fn
    return deco


def _obj(properties, required=None):
    return {"type": "object", "properties": properties, "required": required or []}


@tool("index_repo", "Build/refresh the searchable index of a repository so answers are grounded in its code. Incremental: only changed files are re-embedded; deleted files are pruned.",
      _obj({"path": {"type": "string", "description": "absolute path to the repo"}}, ["path"]))
def _index_repo(a):
    return ENGINE.index_repo(os.path.abspath(a["path"]))


@tool("search_repo", "Search an indexed repo. Returns the most relevant chunks with file path + start/end LINE SPANS.",
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


@tool("set_secret", "Store a secret (encrypted at rest, AES-256-GCM). Use for API keys/tokens the app needs at deploy time. Values are never returned or logged.",
      _obj({"name": {"type": "string"}, "value": {"type": "string"}, "scope": {"type": "string"}}, ["name", "value"]))
def _set_secret(a):
    ENGINE.set_secret(a["name"], a["value"], a.get("scope", "global"))
    return {"ok": True, "name": a["name"]}


@tool("list_secrets", "List secret NAMES (never values) in a scope.",
      _obj({"scope": {"type": "string"}}))
def _list_secrets(a):
    return {"names": ENGINE.list_secrets(a.get("scope", "global")), "vault_backend": vault_backend()}


@tool("deploy_readiness", "Scan a project: deployability INVENTORY (files present) + parsed EVIDENCE (declared scripts, test framework, CI, exact commit). Score reflects inventory, not behavior — call deploy_verify for executed-command evidence.",
      _obj({"path": {"type": "string"}, "run_checks": {"type": "boolean"}}, ["path"]))
def _deploy_readiness(a):
    return deploy_readiness(os.path.abspath(a["path"]), run_checks=bool(a.get("run_checks", False)))


@tool("deploy_verify", "Execute the project's own detected test command(s) (npm test / pytest / unittest / go test / cargo test) and return an evidence manifest bound to the exact commit: commands, exit codes, durations, output tails.",
      _obj({"path": {"type": "string"}, "timeout": {"type": "integer"}}, ["path"]))
def _deploy_verify(a):
    return verify(os.path.abspath(a["path"]), timeout=int(a.get("timeout", 300)))


@tool("scaffold_app", "Write a minimal but genuinely deployable app skeleton (healthcheck + tests + Dockerfile + CI). kind: node-api | python-api | static. REFUSES to overwrite existing files unless replace=true (originals kept as *.engine-ai.bak); dry_run=true previews.",
      _obj({"path": {"type": "string"}, "kind": {"type": "string"},
            "replace": {"type": "boolean"}, "dry_run": {"type": "boolean"}}, ["path", "kind"]))
def _scaffold_app(a):
    return scaffold(os.path.abspath(a["path"]), a["kind"],
                    replace=bool(a.get("replace", False)), dry_run=bool(a.get("dry_run", False)))


@tool("git_publish", "Create a GitHub repo from a folder and push it (idempotent: reuses an existing origin remote instead of re-creating). Requires `gh auth login` first; returns needs_auth with the exact command if not logged in. Ask the user for the repo name before calling.",
      _obj({"path": {"type": "string"}, "repo_name": {"type": "string"}, "private": {"type": "boolean"}}, ["path", "repo_name"]))
def _git_publish(a):
    return git_publish(os.path.abspath(a["path"]), a["repo_name"], private=a.get("private", True))


@tool("vercel_deploy", "Deploy a project to Vercel and return the live URL. Requires `vercel login` or VERCEL_TOKEN; returns needs_auth otherwise. Best for static/Next/frontend apps. Set prod=true for production.",
      _obj({"path": {"type": "string"}, "prod": {"type": "boolean"}}, ["path"]))
def _vercel_deploy(a):
    return vercel_deploy(os.path.abspath(a["path"]), prod=a.get("prod", False))


@tool("responsive_audit", "Static mobile-responsiveness audit of a frontend (viewport meta, media queries, responsive units, flex/grid, responsive images). Returns a score + findings. Static hints only — pair with a real browser check before claiming mobile-ready.",
      _obj({"path": {"type": "string"}}, ["path"]))
def _responsive_audit(a):
    return responsive_audit(os.path.abspath(a["path"]))


@tool("memory_save", "Save a memory pocket: KEYWORDS defining the context + the context text + optional structured data. Pockets are IMMUTABLE and project-scoped — similar keywords never auto-merge; pass supersedes=<id> to explicitly retire an outdated pocket. kind: fact | decision | idea | error | note.",
      _obj({"keywords": {"type": "array", "items": {"type": "string"}}, "context": {"type": "string"},
            "data": {"type": "object"}, "category": {"type": "string"}, "project": {"type": "string"},
            "kind": {"type": "string"}, "source": {"type": "string"},
            "supersedes": {"type": "integer"}}, ["keywords", "context"]))
def _memory_save(a):
    return MEM.save(a["keywords"], a["context"], a.get("data"), a.get("category"),
                    project=a.get("project"), kind=a.get("kind", "note"),
                    source=a.get("source"), supersedes=a.get("supersedes"))


@tool("memory_recall", "Recall the closest memory pockets by KEYWORDS within one PROJECT (hybrid keyword+embedding). Retired (superseded) pockets are excluded; previews are bounded — use memory_get for full text.",
      _obj({"keywords": {"type": "array", "items": {"type": "string"}}, "k": {"type": "integer"},
            "project": {"type": "string"}}, ["keywords"]))
def _memory_recall(a):
    return MEM.recall(a["keywords"], k=int(a.get("k", 3)), project=a.get("project"))


@tool("memory_context", "Return a single ready-to-inject context string built from the closest memory pockets for these keywords (project-scoped) — feed it into a build prompt so work continues from prior decisions.",
      _obj({"keywords": {"type": "array", "items": {"type": "string"}}, "project": {"type": "string"}}, ["keywords"]))
def _memory_context(a):
    return {"context": MEM.context(a["keywords"], project=a.get("project"))}


@tool("memory_get", "Fetch one pocket's FULL text, provenance (source/kind/revision), its supersede chain, and related pockets.",
      _obj({"id": {"type": "integer"}}, ["id"]))
def _memory_get(a):
    return MEM.get(int(a["id"]))


@tool("memory_list", "List memory pockets for a project (keywords, kind, revision, hits, preview).",
      _obj({"project": {"type": "string"}}))
def _memory_list(a):
    return {"pockets": MEM.list(project=a.get("project"))}


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


@tool("app_resume", "Resume a previous app by id or slug — returns its worktree path, branch, recalled memory context, and any drift (branch/folder changed since the session was recorded).",
      _obj({"key": {"type": "string"}}, ["key"]))
def _app_resume(a):
    return SESS.resume(a["key"])


@tool("app_find", "Identify which app session a working directory belongs to (its worktree/branch). Use in /ship-live so shipping targets the right app.",
      _obj({"path": {"type": "string"}}, ["path"]))
def _app_find(a):
    return SESS.find(a["path"])


@tool("knowledge_ingest", "Clone/ingest an engineering repo (URL or local path) into the domain-tagged knowledge store so domain agents can retrieve it. Records the source commit; refresh=true fetches+resets an existing clone first.",
      _obj({"source": {"type": "string"}, "domain": {"type": "string"}, "repo": {"type": "string"},
            "refresh": {"type": "boolean"}}, ["source", "domain"]))
def _knowledge_ingest(a):
    return KN.ingest_repo(a["source"], a["domain"], a.get("repo"), refresh=bool(a.get("refresh", False)))


@tool("knowledge_search", "Search the ingested engineering knowledge (roadmaps, system design, ML, LLM, build-your-own-x, …), optionally filtered by domain. Returns the most relevant chunks with source.",
      _obj({"query": {"type": "string"}, "domain": {"type": "string"}, "k": {"type": "integer"}}, ["query"]))
def _knowledge_search(a):
    return {"hits": KN.search(a["query"], domain=a.get("domain"), k=int(a.get("k", 6)))}


@tool("knowledge_domains", "List the knowledge domains available (and their chunk/repo counts) + ingested sources with freshness (commit + age).", _obj({}))
def _knowledge_domains(a):
    return {"domains": KN.domains(), "sources": KN.sources()}


@tool("context_pack", "Assemble a BUDGETED context pack for a task: prior project MEMORY + retrieved DOMAIN KNOWLEDGE, compiled within a token budget (default ~6k tokens) so prompts stay bounded. Returns context + measured token count.",
      _obj({"keywords": {"type": "array", "items": {"type": "string"}}, "domain": {"type": "string"},
            "budget_tokens": {"type": "integer"}}, ["keywords"]))
def _context_pack(a):
    return KN.context_pack(a["keywords"], domain=a.get("domain"),
                           budget_tokens=int(a.get("budget_tokens", 6000)))


@tool("suggest_experts", "Deterministically route a request to the domain expert(s) to consult (ranked domain-<slug> agents, word-boundary phrase matching, max 5). Also classifies the task profile (design/implement/review/research) and maps each expert to an engine_core worker profile.",
      _obj({"request": {"type": "string"}, "k": {"type": "integer"}}, ["request"]))
def _suggest_experts(a):
    return _experts.suggest(a["request"], k=int(a.get("k", 3)), knowledge=KN)


@tool("web_search", "Live web search for current, real-time information the offline knowledge store can't have (recent releases, current docs/pricing, breaking changes, news). Uses Brave Search if a BRAVE_API_KEY secret is set (set_secret), else falls back to DuckDuckGo — no key required either way. Returns title/url/snippet hits.",
      _obj({"query": {"type": "string"}, "k": {"type": "integer"}}, ["query"]))
def _web_search(a):
    return {"hits": WS.search(a["query"], k=int(a.get("k", 5))), "provider": WS.status()["active_provider"]}


@tool("web_search_status", "Check which web_search provider is active (brave vs duckduckgo) and whether a Brave API key is configured for higher-quality results.", _obj({}))
def _web_search_status(a):
    return WS.status()


@tool("so_search", "Search StackOverflow for real-world Q&A on a topic (design precedent, 'how do others solve X', library usage). Optionally filter by tag (e.g. 'python'). Higher quota with a STACKOVERFLOW_API_KEY secret set (set_secret), works without one too. Returns title/link/score/tags/question_id per hit.",
      _obj({"query": {"type": "string"}, "tagged": {"type": "string"}, "k": {"type": "integer"}}, ["query"]))
def _so_search(a):
    return {"hits": SO.search_questions(a["query"], tagged=a.get("tagged"), k=int(a.get("k", 5)))}


@tool("so_debug", "Given an error message or full traceback, find the most relevant StackOverflow questions and each one's top-voted/accepted answer — ready-to-cite fixes for debugging. Auto-detects the language (python/javascript/java/go/rust) from the traceback format for better relevance; pass `language` to override.",
      _obj({"error_text": {"type": "string"}, "language": {"type": "string"}, "k": {"type": "integer"}}, ["error_text"]))
def _so_debug(a):
    return {"hits": SO.debug(a["error_text"], language=a.get("language"), k=int(a.get("k", 5)))}


# ----------------------------------------------------------- per-agent OpenRouter keys
@tool("openrouter_setup", "Check per-agent OpenRouter API-key + model assignment (10 profiles: planner, frontend, backend, …). If any key or model is missing, returns ask_user instructions — PROMPT THE USER for `key,model` lines (one per profile, or `profile,key,model`), or a CSV file path, then call openrouter_import. Permanent once set.",
      _obj({}))
def _openrouter_setup(a):
    from engine_core.profiles import Assignments
    return Assignments(HOME).setup_payload()


@tool("openrouter_import", "Import OpenRouter API keys + models PERMANENTLY: pass `csv` with pasted lines `key,model` (or `profile,key,model`), or `path` to a CSV file. Keys go into the encrypted vault (AES-256-GCM); the profile's model is saved alongside. 2-column lines fill the missing profiles in order, or name one with `profile`. Use after asking the user (see openrouter_setup).",
      _obj({"csv": {"type": "string"}, "path": {"type": "string"}, "profile": {"type": "string"}}, []))
def _openrouter_import(a):
    if not a.get("csv") and not a.get("path"):
        return {"ok": False, "error": "pass csv (pasted `key,model` lines) or path (CSV file)"}
    from engine_core.profiles import Assignments
    return Assignments(HOME).import_keys(text=a.get("csv"), path=a.get("path"),
                                         profile=a.get("profile"),
                                         set_secret=ENGINE.set_secret,
                                         get_secret=ENGINE.get_secret)


@tool("codex_setup", "Generate Codex CLI config (~/.codex/config.toml) from the configured engine profiles: one OpenRouter model_provider + profile selector per agent key/model, plus a chmod-600 env file with the key exports. NEVER touches ~/.codex/auth.json (your OpenAI login is preserved); existing config.toml content outside the engine-ai block is kept, with a backup. Run openrouter_setup first — needs at least one configured profile.",
      _obj({"codex_dir": {"type": "string"}, "active": {"type": "string"},
            "write_env": {"type": "boolean"}}, []))
def _codex_setup(a):
    from engine_core import codex_config
    from engine_core.profiles import Assignments
    assignments = Assignments(HOME)
    payload = assignments.setup_payload()
    if payload["missing"]:
        return {"ok": False, "error": "not all profiles are configured — run "
                                      "openrouter_setup and import keys first",
                "missing": payload["missing"]}
    configured = [r for r in payload["profiles"] if r["key"] != "MISSING"]
    # resolve actual model ids (catalog refs must already be pinned/imported)
    resolved = []
    for r in configured:
        model = r.get("model")
        if not model or str(model).startswith("✗"):
            model = r["model_ref"]
        if not model or str(model).startswith("catalog:"):
            return {"ok": False, "error": f"profile {r['profile']!r} has no concrete model "
                                          f"({r['model_ref']!r}) — re-import with key,model",
                    "missing": [r["profile"]]}
        resolved.append({**r, "model": model})
    codex_dir = a.get("codex_dir") or os.path.join(os.path.expanduser("~"), ".codex")
    out = codex_config.write_config(codex_dir, resolved, active=a.get("active"))
    if a.get("write_env", True):
        keys = {}
        for r in resolved:
            ref = r.get("credential_ref") or ""
            name = ref[len("vault:"):] if ref.startswith("vault:") else None
            key = ENGINE.get_secret(name) if name else None
            if key:
                keys[r["profile"]] = key
        if keys:
            out["env"] = codex_config.write_env_file(codex_dir, keys)
    out["auth_json"] = "NOT modified (your OpenAI login is preserved)"
    out["next"] = ("source ~/.codex/engine-ai-env.sh (or add it to your shell profile), "
                   "then: codex --profile engine_" +
                   (a.get("active") or resolved[0]["profile"]).replace("-", "_"))
    return out


# ----------------------------------------------------------- request validation
_TYPES = {"string": str, "integer": int, "boolean": bool, "object": dict,
          "array": list, "number": (int, float)}


def _validate_args(name: str, args) -> str | None:
    """Check `args` against the tool's inputSchema; return an error string or None."""
    schema = next((t["inputSchema"] for t in TOOLS if t["name"] == name), None)
    if not isinstance(args, dict):
        return "arguments must be an object"
    if schema is None:
        return None
    for req in schema.get("required", []):
        if req not in args or args[req] in (None, ""):
            return f"missing required argument: {req}"
    for key, spec in (schema.get("properties") or {}).items():
        val = args.get(key)
        if val is None:
            continue
        want = spec.get("type")
        if not want:
            continue
        py = _TYPES.get(want)
        if py is bool and isinstance(val, bool):
            continue
        if py is int and isinstance(val, bool):  # bool is an int subclass — reject for integers
            return f"{key} must be an integer"
        if py and not isinstance(val, py):
            return f"{key} must be {want}"
    return None


# ----------------------------------------------------------- JSON-RPC plumbing
def _result(id_, result):
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _error(id_, code, message):
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def handle(msg) -> dict | None:
    if not isinstance(msg, dict):
        return _error(None, -32600, "invalid request: expected a JSON-RPC object")
    method = msg.get("method")
    id_ = msg.get("id")
    if not isinstance(method, str) or not method:
        return _error(id_, -32600, "invalid request: missing method") if id_ is not None else None

    if method == "initialize":
        params = msg.get("params") or {}
        requested = params.get("protocolVersion", PROTOCOLS[-1])
        negotiated = requested if requested in PROTOCOLS else PROTOCOLS[-1]
        return _result(id_, {
            "protocolVersion": negotiated,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "engine-ai", "version": _version()},
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
        if not isinstance(name, str) or not name:
            return _error(id_, -32602, "tools/call requires a tool name")
        args = params.get("arguments") or {}
        fn = HANDLERS.get(name)
        if not fn:
            return _error(id_, -32601, f"unknown tool: {name}")
        invalid = _validate_args(name, args)
        if invalid:
            return _error(id_, -32602, f"invalid arguments for {name}: {invalid}")
        try:
            out = fn(args)
            # operational outcomes (ok:false etc.) are normal results, NOT protocol errors
            return _result(id_, {"content": [{"type": "text", "text": json.dumps(out, indent=2)}]})
        except Exception as e:  # noqa: BLE001 - tool execution error per MCP
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
        except json.JSONDecodeError as e:
            sys.stdout.write(json.dumps(_error(None, -32700, f"parse error: {e}")) + "\n")
            sys.stdout.flush()
            continue
        resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
