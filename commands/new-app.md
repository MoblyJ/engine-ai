---
description: Build a new deployable app in an ISOLATED engine-ai session — own workspace, full MCP tools, A2A agents, and automatic evolving memory.
---

Build a new deployable application based on: $ARGUMENTS

Run this as an **isolated engine-ai flow** — do not build inline in the current project. Two isolation
steps, then delegate the whole build to the orchestrator agent:

### 1. Isolate the workspace (filesystem)
- Derive a short **slug** from the request (e.g. "free gaming landing page" → `gaming-landing`).
- Create a dedicated project folder for it: `./<slug>/` (or `apps/<slug>/` if that dir exists). All
  scaffolding and edits happen there — never in the toolkit repo or the user's unrelated files.

### 2. Isolate the session (context) — delegate to the orchestrator
- Hand the request off to the **`engine-orchestrator`** agent via the Task tool, with:
  - the user's request (`$ARGUMENTS`),
  - the isolated workspace path from step 1,
  - instruction to run its full **A2A loop**.
- The orchestrator runs in its own context and already has the engine-ai MCP tools + coordinates the
  sub-agents. It will:
  1. **`memory_recall`/`memory_context`** on keywords from the request (evolve prior work).
  2. **`engine-grounder`** — index/search if building on existing code.
  3. **`engine-app-builder`** — `scaffold_app` → implement → tests → `deploy_readiness` (100).
  4. **`engine-mobile`** — `responsive_audit` if there's a UI.
  5. **`engine-deployer`** — only if the user asks to publish/deploy (asks for the repo name).
  6. **`memory_save`** — keywords + what was built + structured data (so the next build evolves).

### 3. Report back
Summarize: the isolated workspace path, what was built, test + readiness results, which agents ran,
and what memory pocket was recalled/saved.

The task is not done until it ran in its own workspace **and** the memory recall (step 2.1) + save
(step 2.6) both happened.
