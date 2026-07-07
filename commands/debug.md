---
description: Fix a real error/exception/failing test, grounded in cited StackOverflow precedent — session-aware, with evolving memory (uses the debugging skill).
---

Debug and fix: $ARGUMENTS

Run this **session-aware** so known fixes persist and evolve:

### 0. Recall memory + ground
- Call `memory_context({ keywords })` — this exact error may already have a known fix from a prior
  session in this app.
- If this is an existing project, `search_repo` for the failing code's context first.

### 1–3. Search → apply → verify
1. Call `so_debug({ error_text })` with the **full error message or traceback verbatim** — don't
   summarize it first, that loses the exact signal search needs. For general "how do others do X"
   precedent not tied to one error, use `so_search({ query, tagged })` instead.
2. Prefer the accepted answer, then the highest-scored one. Apply the **minimal fix that addresses
   the root cause**, adapted to this codebase's conventions — never copy-paste a snippet verbatim
   without adapting it.
3. **Re-run the original failing test/command yourself.** Do not report fixed until it actually passes.

### 4. Save the fix to memory
- `memory_save({ keywords, context: "<root cause + fix>", data: { urls: [<source answer urls>] } })`
  — so a recurrence of this exact error is instantly resolved next time.

Not done until you searched the actual error (not a summary of it), applied and adapted a fix,
re-ran the failing case and confirmed it passes, and saved the fix to memory.
