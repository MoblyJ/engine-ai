---
description: Index the repo and work grounded in its real code (RAG) — session-aware, with evolving memory.
---

Ground your work in this repository before acting on: $ARGUMENTS

Run this **session-aware** so grounding persists and evolves:

### 0. Locate the app + recall memory
- Call `app_find({ path: <cwd> })`. If it matches an app session, use its `path` and `keywords`;
  otherwise use the cwd and derive keywords from the request.
- Call `memory_context({ keywords })` — pull any prior grounded context/decisions so you don't
  re-discover what's already known.

### 1–3. Index → search → use
1. Call `index_repo(path)` on the working directory (absolute path).
2. Call `search_repo(path, query)` with the key terms from the request to retrieve the most relevant
   code/docs.
3. Use the retrieved chunks (cite file paths) as the basis for your answer or change — do not guess at
   the codebase when you can search it.

### 4. Save the grounding to memory
- `memory_save({ keywords, context: "<key files + what they do, relevant to the task>", data: { "grounded_files": [<paths>] } })` — so the next prompt on this app reuses the grounding instead of re-indexing from scratch.
- If an app session matched, optionally `app_update({ id, summary })` to note what area was grounded.

Not done until you indexed, searched, grounded your work in real files, and saved the grounding to
memory.
