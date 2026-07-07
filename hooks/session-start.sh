#!/usr/bin/env bash
# engine-ai activation hook (Claude Code SessionStart).
# Emits additionalContext so every new session knows the toolkit is active and how to use it.
# Output contract: JSON on stdout that Claude Code injects as context.
set -euo pipefail

read -r -d '' CTX <<'EOF' || true
engine-ai is ACTIVE. MCP server "engine-ai" tools:
  build/ship: scaffold_app, deploy_readiness, responsive_audit, git_publish, vercel_deploy
  repo/skills: index_repo, search_repo, list_skills, get_skill, import_repo_skills
  secrets: set_secret, list_secrets
  memory: memory_save, memory_recall, memory_context (keyword-tagged EVOLVING context)
  app sessions (git worktrees): app_create, app_update, app_list, app_resume, app_find
  knowledge swarm: knowledge_ingest, knowledge_search, knowledge_domains, context_pack
  web search: web_search, web_search_status (live, current info — Brave if keyed, else DuckDuckGo)
Slash commands: /new-app (isolated worktree build), /resume-app, /mobile-check, /deploy-check,
/ground, /ship-live, /expert (ask a domain expert grounded in ingested knowledge),
/research (live web search for current info, cited).
Subagents in /agents: engine-orchestrator + engine-{app-builder,mobile,deployer,grounder,memory,
researcher}, and ~30 domain-<slug> experts (frontend, backend, system-design, machine-learning,
llm, security…) — every domain expert also has web_search for anything time-sensitive.
RULE — grounding/research agents run FIRST, always, before writing, planning, or executing any
code: recall memory (memory_context/memory_recall), ground in the real repo (index_repo/
search_repo or engine-grounder), and research anything time-sensitive (web_search or
engine-researcher) BEFORE the first line of a plan or a diff. Never start implementation before
that context is assembled.
FLOW: for any build/answer, first call context_pack(keywords) to assemble prior MEMORY + retrieved
DOMAIN KNOWLEDGE (call web_search too if part of the task is time-sensitive), then act, then
memory_save. Building an app? Follow the deployable-app skill and finish only when tests pass,
deploy_readiness=100, and the container answers /healthz.
EOF

if command -v jq >/dev/null 2>&1; then
  jq -n --arg c "$CTX" '{hookSpecificOutput:{hookEventName:"SessionStart",additionalContext:$c}}'
else
  # jq missing: emit minimal valid JSON via python3 (always present in this toolkit)
  python3 - "$CTX" <<'PY'
import json, sys
print(json.dumps({"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":sys.argv[1]}}))
PY
fi
