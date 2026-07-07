#!/usr/bin/env bash
# engine-ai installer — integrates the toolkit into Claude Code on WSL.
#
#   ./install.sh                 install (detects Claude Code, wires skills/commands/hook/MCP)
#   ./install.sh --import-siblings   also import skills from ../agent-skills ../gstack ../oh-my-pi
#   ./install.sh --uninstall     remove everything this installer added
#
# Idempotent. Backs up ~/.claude/settings.json before editing. Pure bash + python3.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SKILLS_DIR="$CLAUDE_DIR/skills"
CMDS_DIR="$CLAUDE_DIR/commands"
AGENTS_DIR="$CLAUDE_DIR/agents"
SETTINGS="$CLAUDE_DIR/settings.json"
HOOK="$REPO_DIR/hooks/session-start.sh"
MCP_NAME="engine-ai"
MCP_CMD=(python3 "$REPO_DIR/mcp/forge_mcp.py")

c() { printf '\033[%sm%s\033[0m\n' "$1" "$2"; }
info() { c "36" "• $1"; }
ok()   { c "32" "✓ $1"; }
warn() { c "33" "! $1"; }
err()  { c "31" "✗ $1"; }

detect_wsl() {
  if grep -qi microsoft /proc/version 2>/dev/null; then ok "WSL detected"; else
    warn "not WSL (continuing anyway)"; fi
}

detect_claude() {
  if command -v claude >/dev/null 2>&1; then
    ok "Claude Code found: $(command -v claude) ($(claude --version 2>/dev/null | head -1))"
    return 0
  fi
  err "Claude Code is NOT installed."
  echo "  Install it first, then re-run this:"
  echo "    npm install -g @anthropic-ai/claude-code      # or:"
  echo "    curl -fsSL https://claude.ai/install.sh | bash"
  return 1
}

merge_hook() { # add or remove our SessionStart hook in settings.json (valid JSON either way)
  local action="$1"
  mkdir -p "$CLAUDE_DIR"
  [ -f "$SETTINGS" ] || echo '{}' > "$SETTINGS"
  cp "$SETTINGS" "$SETTINGS.scc-backup.$(date +%s)" 2>/dev/null || true
  python3 - "$SETTINGS" "$HOOK" "$action" <<'PY'
import json, sys
path, hook, action = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    cfg = json.load(open(path))
except Exception:
    cfg = {}
cmd = f"bash {hook}"
hooks = cfg.setdefault("hooks", {})
ss = hooks.setdefault("SessionStart", [])
# Drop any existing entry that references OUR hook script — matched by the
# stable "hooks/session-start.sh" suffix, not the full absolute path. The
# absolute path changes across reinstalls (nvm gives each Node version its
# own global node_modules dir, and repeated uninstall/reinstall cycles can
# each land at a different location), so an exact-string match here would
# never recognize an old install's entry as "ours" and would just keep
# appending a new one forever, leaving stale entries pointing at directories
# that no longer exist. This also retroactively cleans up any such stale
# entries already accumulated in settings.json the next time this runs.
MARKER = "hooks/session-start.sh"
def has_ours(group):
    return any(MARKER in h.get("command", "") for h in group.get("hooks", []))
ss = [g for g in ss if not has_ours(g)]
if action == "add":
    ss.append({"hooks":[{"type":"command","command":cmd}]})
hooks["SessionStart"] = ss
if not ss: hooks.pop("SessionStart", None)
if not hooks: cfg.pop("hooks", None)
json.dump(cfg, open(path,"w"), indent=2)
print("hook " + ("added" if action=="add" else "removed"))
PY
}

link_dir() { # link_dir <srcdir> <destdir> <suffix-filter>
  local src="$1" dest="$2" filt="$3"
  mkdir -p "$dest"
  for item in "$src"/*; do
    [ -e "$item" ] || continue
    local base; base="$(basename "$item")"
    if [ -n "$filt" ]; then case "$base" in *$filt) ;; *) continue;; esac; fi
    ln -sfn "$item" "$dest/$base"
  done
}

unlink_from() { # remove symlinks in <destdir> that point into our repo
  local dest="$1"
  [ -d "$dest" ] || return 0
  for item in "$dest"/*; do
    [ -L "$item" ] || continue
    local tgt; tgt="$(readlink "$item")"
    case "$tgt" in "$REPO_DIR"/*) rm -f "$item";; esac
  done
}

register_mcp() {
  claude mcp remove -s user "$MCP_NAME" >/dev/null 2>&1 || claude mcp remove "$MCP_NAME" >/dev/null 2>&1 || true
  if claude mcp add -s user "$MCP_NAME" -- "${MCP_CMD[@]}" >/dev/null 2>&1; then
    ok "MCP server '$MCP_NAME' registered (user scope)"
  elif claude mcp add "$MCP_NAME" -- "${MCP_CMD[@]}" >/dev/null 2>&1; then
    ok "MCP server '$MCP_NAME' registered"
  else
    warn "could not auto-register MCP; add manually:"
    echo "    claude mcp add $MCP_NAME -- ${MCP_CMD[*]}"
  fi
}

do_install() {
  info "engine-ai installer"
  detect_wsl
  detect_claude || exit 1
  link_dir "$REPO_DIR/skills" "$SKILLS_DIR" ""
  ok "skills linked → $SKILLS_DIR"
  link_dir "$REPO_DIR/commands" "$CMDS_DIR" ".md"
  ok "commands linked → $CMDS_DIR"
  link_dir "$REPO_DIR/agents" "$AGENTS_DIR" ".md"
  ok "agents linked → $AGENTS_DIR (shows in Claude Code /agents)"
  chmod +x "$HOOK" "$REPO_DIR/mcp/forge_mcp.py" 2>/dev/null || true
  merge_hook add >/dev/null && ok "SessionStart activation hook installed"
  register_mcp
  if [ "${1:-}" = "--import-siblings" ]; then
    info "importing skills from sibling repos…"
    python3 - "$REPO_DIR" <<'PY'
import os, sys
sys.path.insert(0, os.path.join(sys.argv[1], "mcp"))
from engine import Engine
e = Engine(); base = os.path.dirname(os.path.abspath(sys.argv[1]))
total = 0
for r in ("agent-skills","gstack","oh-my-pi"):
    p = os.path.join(base, r)
    if os.path.isdir(p):
        n = e.import_repo_skills(p); total += n; print(f"  {r}: {n}")
print(f"  total skills now: {len(e.list_skills())}")
PY
  fi
  echo
  ok "Installed. Restart Claude Code (or open a new session)."
  echo "  Try:  /new-app a node API that returns the current time"
  echo "        /deploy-check ."
  echo "        ask Claude to 'list_skills' or 'scaffold_app'."
}

do_uninstall() {
  info "uninstalling engine-ai"
  unlink_from "$SKILLS_DIR"; ok "skills unlinked"
  unlink_from "$CMDS_DIR"; ok "commands unlinked"
  unlink_from "$AGENTS_DIR"; ok "agents unlinked"
  merge_hook remove >/dev/null && ok "activation hook removed"
  claude mcp remove -s user "$MCP_NAME" >/dev/null 2>&1 || claude mcp remove "$MCP_NAME" >/dev/null 2>&1 || true
  ok "MCP server removed"
  ok "done (settings backups kept as $SETTINGS.scc-backup.*)"
}

case "${1:-}" in
  --uninstall) do_uninstall ;;
  *) do_install "${1:-}" ;;
esac
