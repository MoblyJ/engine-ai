---
description: Resume a previous app — lists past sessions with a 2-line summary; reopens the worktree, folder, and its memory context to continue.
---

Resume a previous app session${ARGUMENTS:+ matching: $ARGUMENTS}.

### 1. List the sessions
- Call the MCP tool `app_list`. Present the sessions as a **numbered list**, each with its **two
  lines**:
  - **line 1:** `#<id> · <name> · opened <when> · <path>`
  - **line 2:** the saved summary (or its keywords)
- Example:
  ```
  1) #3 · Gaming Landing · opened 2h ago · ~/.engine-ai/apps/gaming-landing
     Free-download gaming site: hero livestream, XP, mobile-responsive. Static.
  2) #1 · Time API · opened 1d ago · ~/.engine-ai/apps/time-api
     Node API with GET /time + /healthz. Dockerized, tests pass.
  ```
- If `$ARGUMENTS` is given, pre-filter the list to matching name/keywords. Ask the user which number
  (or id/slug) to resume. If there are none, tell them to start one with `/new-app`.

### 2. Resume it
- Call `app_resume({ key: <id or slug> })`. It returns the worktree `path`, `branch`, the saved
  `summary`, and the **recalled `memory_context`** for that app.
- `cd` into the returned `path` (the worktree). Load the `memory_context` so you continue with the
  app's prior context, decisions, and data — **do not start over**.
- If `exists` is false (folder was removed), tell the user; offer to rebuild from the memory context.

### 3. Continue
Summarize where the app left off (from the summary + memory), then proceed with whatever the user now
wants — building on the existing files and context. Save memory + `app_update` again when done.
