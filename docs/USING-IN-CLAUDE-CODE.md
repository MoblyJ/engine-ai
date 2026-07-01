# Using engine-ai in Claude Code — build full deployable apps

This toolkit installs **into your existing Claude Code on WSL** and gives every session a set of
slash commands, a skill, and an MCP server it can call. This guide shows how to use them to take an
idea all the way to a **deployable** app.

---

## What got installed (and how to confirm)

The installer (`./install.sh`) wired four things into `~/.claude`:

| Thing | Where | Confirm |
|---|---|---|
| **MCP server** (the tools) | registered user-scope | `claude mcp list` → `engine-ai … ✔ Connected` |
| **Skill** `deployable-app` | `~/.claude/skills/` | `/help` or it auto-triggers on "build an app" |
| **Slash commands** | `~/.claude/commands/` | type `/` → `new-app`, `deploy-check`, `ground` |
| **Activation hook** | `~/.claude/settings.json` (SessionStart) | new sessions get a one-line "toolkit active" context |

After installing, **start a new Claude Code session** (the MCP server + hook load at startup).

---

## The tools your agent can now call (MCP)

| Tool | Use it to… |
|---|---|
| `scaffold_app(path, kind)` | write a deployable skeleton — `node-api`, `python-api`, or `static` (healthcheck + test + Dockerfile + CI) |
| `deploy_readiness(path)` | score a project's deployability and list exactly what's missing |
| `index_repo(path)` / `search_repo(path, query)` | ground work in an existing codebase (repo-aware retrieval) |
| `list_skills(query)` / `get_skill(name)` | the unified workflow library (80 skills imported from agent-skills + gstack + oh-my-pi) |
| `set_secret(name, value)` / `list_secrets()` | keep API keys out of code (encrypted at rest) |
| `import_repo_skills(path)` | ingest more SKILL.md skills from any repo |

You don't call these by hand — you ask Claude in plain English ("scaffold a node API", "is this repo
deployable?") and it invokes the tool.

---

## Slash commands

- **`/new-app <description>`** — scaffold + build a deployable app end-to-end.
- **`/deploy-check [path]`** — audit a project's deployability and fix the gaps.
- **`/ground <task>`** — index the current repo and work grounded in its real code.

---

## Worked example — idea → deployed

In a new Claude Code session, in an empty folder:

```
You:  /new-app a Node API with GET /time returning the current ISO timestamp
```
Claude will (following the `deployable-app` skill):
1. **scaffold** — calls `scaffold_app(".", "node-api")` → server.js, test, Dockerfile, CI, .env.example.
2. **implement** — adds the `/time` route, keeps `/healthz`.
3. **test** — runs `node --test`; adds a test for `/time`.
4. **readiness** — calls `deploy_readiness(".")`; fixes anything below score 100.
5. **secrets** — if you need any (e.g. an API key), `set_secret` + document in `.env.example`.
6. **ship** — `docker build`, run, `curl /healthz`, then commit / open a PR.

Then:
```
You:  containerize and run it, then show me the healthcheck
You:  /deploy-check .          # confirm score 100 before pushing
```

### Working on an existing repo
```
You:  /ground add rate limiting to the login endpoint
```
Claude indexes the repo, `search_repo`s for the login/auth code, and edits the real files instead of
guessing.

---

## The "deployable" bar (what the skill enforces)
A build isn't done until:
- [ ] tests pass
- [ ] `deploy_readiness` score is 100 (Dockerfile, tests, CI, `.env.example`, README, healthcheck)
- [ ] `docker build` succeeds and the container answers `/healthz`
- [ ] required env vars are in `.env.example`; **no secrets committed**

---

## Troubleshooting
- **`claude mcp list` doesn't show it / not Connected** → re-run `./install.sh`; ensure `python3` is
  on PATH; check `python3 mcp/forge_mcp.py` starts without error.
- **Commands don't appear** → start a *new* session; confirm symlinks in `~/.claude/commands/`.
- **Hook didn't fire** → check `~/.claude/settings.json` has the SessionStart entry (a timestamped
  backup `settings.json.scc-backup.*` was made before editing).
- **Remove everything** → `./install.sh --uninstall` (reverses links, hook, and MCP registration).

---

## How this relates to the bigger picture
This implements the useful parts of the hosting-platform concept spec'd in `../all.md` (§6) —
unified skills, repo RAG, secrets, deployability — but with **no web control plane**. Instead of a
server you host and a browser you open, it plugs straight into the Claude Code you already run in
your terminal. (Hosting-only concerns — multi-user auth, a job queue, a dashboard — are out of scope
by design and remain spec-only.)
