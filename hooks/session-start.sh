#!/usr/bin/env bash
# engine-ai activation hook (Claude Code SessionStart).
# Emits additionalContext so every new session knows the toolkit is active and how to use it.
# Output contract: JSON on stdout that Claude Code injects as context.
set -euo pipefail

read -r -d '' CTX <<'EOF' || true
engine-ai is ACTIVE. MCP server "engine-ai" tools:
  build/ship: scaffold_app (refuses overwrite; dry_run), deploy_readiness (inventory+evidence),
    deploy_verify (EXECUTES the project's own tests, evidence bound to the commit),
    responsive_audit, git_publish (idempotent, worktree-aware), vercel_deploy
  repo/skills: index_repo (incremental), search_repo (line spans), list_skills, get_skill,
    import_repo_skills
  secrets: set_secret, list_secrets (AES-256-GCM vault)
  memory: memory_save (project-scoped, immutable, kind=fact/decision/idea/error, supersedes=),
    memory_recall/memory_context (one project, bounded previews), memory_get (full text + chain),
    memory_list, memory_forget
  app sessions (git worktrees): app_create, app_update, app_list, app_resume, app_find
  knowledge swarm: knowledge_ingest (refresh=), knowledge_search, knowledge_domains, context_pack
    (BUDGETED — default ~6k tokens)
  web search: web_search, web_search_status (live, current info — Brave if keyed, else DuckDuckGo)
  stackoverflow: so_search, so_debug (real-world Q&A; so_debug takes an error/traceback, auto-detects
    language, returns the top matching questions + accepted/top answers, cited)
Slash commands: /new-app (isolated worktree build), /resume-app, /mobile-check, /deploy-check,
/ground, /ship-live, /expert (ask a domain expert grounded in ingested knowledge),
/research (live web search for current info, cited), /debug (fix a real error via StackOverflow,
cited, verified).
Subagents in /agents: engine-orchestrator + engine-{app-builder,mobile,deployer,grounder,memory,
researcher,debugger}, and ~30 domain-<slug> experts (frontend, backend, system-design,
machine-learning, llm, security…) — every domain expert also has web_search for anything
time-sensitive. engine-researcher also designs OOP class architecture (Mermaid diagrams,
precedent-grounded via web_search+so_search) before non-trivial builds; engine-debugger fixes real
errors via so_debug, grounded in cited StackOverflow answers, and verifies the fix actually passes.
RULES —
1. Ground before acting: recall memory (memory_context/memory_recall — project-scoped), ground in
   the real repo (index_repo/search_repo or engine-grounder), research only what is time-sensitive
   (web_search/engine-researcher). Match the altitude: a small change needs memory + grounding, NOT
   three experts or a class diagram; summon experts (suggest_experts, max ~3) only for non-trivial
   work that matched their domain.
2. Memory hygiene: save kind=decision for choices, kind=fact for verified outcomes, kind=error for
   pitfalls; supersede outdated pockets explicitly (supersedes=old_id) — never save a contradictory
   twin. A model claim is not verified evidence.
3. Verify with evidence, not scores: deploy_readiness is inventory; call deploy_verify and require
   the project's own test command to exit 0 at the current commit before calling something done.
4. Hit a real error? so_debug(error_text) before guessing at a fix.
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
