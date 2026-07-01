---
description: Build a new deployable app in a hard-isolated GIT WORKTREE session — own branch + folder, full MCP tools, A2A agents, and evolving memory. Resumable via /resume-app.
---

Build a new deployable application based on: $ARGUMENTS

Run this as an **isolated, resumable engine-ai session**:

### 1. Create the isolated worktree session
- Pick a short **name** and 3–6 **keywords** from the request.
- Call the MCP tool `app_create({ name, keywords })`. This creates a **git worktree** — its own branch
  (`app/<slug>`) and its own folder — and returns the `path`. **All work happens in that `path`**,
  fully isolated from the current repo and other apps. (If git isn't available it falls back to a
  standalone repo/folder; the returned `path` is always where you build.)

### 2. Build there — delegate to the orchestrator
- Hand off to the **`engine-orchestrator`** agent (Task tool) with the request + the worktree `path` +
  the session `id`. It runs its full **A2A loop** in its own context, with all engine-ai tools/agents:
  1. `context_pack` on the keywords (evolve prior work + retrieved domain knowledge).
  2. **`suggest_experts({ request })`** → consult the returned `domain-<slug>` experts (e.g. a chat
     app → `backend` + `security` + `system-design`) for grounded, cited design guidance; fold into the brief.
  3. `engine-grounder` → `engine-app-builder` (`scaffold_app` → implement → tests → `deploy_readiness` 100).
  4. `engine-mobile` if there's a UI; `engine-deployer` only if the user asks to ship.

### 3. Save session + memory (so it's resumable)
- `memory_save(keywords, context, data)` — the evolving memory pocket.
- `app_update({ id, summary, keywords })` — a **2-line summary** of what this app is + its keywords, so
  it shows nicely in `/resume-app`.

### 4. Report
The worktree path + branch, what was built, tests + readiness, agents that ran, and that the session
was saved (resume later with `/resume-app`).

Not done until it built in its own worktree **and** memory + the session summary were saved.
