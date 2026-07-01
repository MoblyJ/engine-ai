#!/usr/bin/env bash
# mobly-ai activation hook (Claude Code SessionStart).
# Emits additionalContext so every new session knows the toolkit is active and how to use it.
# Output contract: JSON on stdout that Claude Code injects as context.
set -euo pipefail

read -r -d '' CTX <<'EOF' || true
mobly-ai is ACTIVE. You have an MCP server "mobly-ai" with tools:
  scaffold_app, deploy_readiness, index_repo, search_repo, list_skills, get_skill,
  import_repo_skills, set_secret, list_secrets.
Slash commands: /new-app (build a deployable app), /deploy-check (audit deployability),
/ground (index + search this repo). Skill: deployable-app.
When asked to build an app or make one deployable, follow the deployable-app skill and finish only
when tests pass, deploy_readiness scores 100, and the container answers /healthz.
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
