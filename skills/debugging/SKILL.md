---
name: debugging
description: Fix a real error/exception/failing test by grounding the fix in StackOverflow precedent (real-world accepted answers, cited) instead of guessing. Use when there's an actual error message, traceback, or stack trace to fix — not for general code review or building new features from scratch.
---

# Debugging

Don't guess at a fix from memory alone — **search StackOverflow for the actual error**, read what
worked for others (accepted/top-voted answers), then apply and verify the fix.

## When to use
- There's a concrete error message, exception, traceback, or failing test to fix.
- NOT for building new features (`deployable-app`) or open-ended design questions (`expert-answer`)
  — this skill is specifically for "this broke, here's the error, fix it."

## Method
1. **Ground first** — if this is an existing project, `search_repo`/`engine-grounder` for the
   failing code's context before touching anything.
2. **Search** — call `so_debug({ error_text })` with the full error message or traceback (paste it
   verbatim; don't summarize it first — the language is auto-detected from the traceback format for
   better relevance). For broader "how do I do X" precedent (not tied to one error), use
   `so_search({ query, tagged })` instead.
3. **Evaluate answers** — prefer the **accepted** answer, then the highest-scored one. Read more than
   one hit if the top one doesn't clearly apply to this exact situation — StackOverflow answers vary
   in quality and can be outdated.
4. **Apply the fix** — make the minimal change that addresses the root cause, not just the symptom.
   Don't copy-paste a StackOverflow snippet verbatim without adapting it to this codebase's
   conventions (naming, error handling, types).
5. **Verify** — re-run the failing test/command. Don't declare it fixed until it actually passes.
6. **Cite + remember** — note which StackOverflow answer(s) informed the fix (URL), and
   `memory_save([...error keywords], "<root cause + fix>", { urls: [...] })` so a recurrence of this
   exact error is instantly resolved next time.

## Red flags
- Applying a fix without first searching for the actual error (guessing from general knowledge).
- Copy-pasting an answer's code verbatim without adapting it or verifying it actually fixes this case.
- Declaring victory without re-running the test/reproduction that originally failed.

## Verification
- [ ] Searched the actual error text via `so_debug` (or `so_search` for precedent), not summarized
- [ ] Fix addresses the root cause, adapted to this codebase, not a blind copy-paste
- [ ] Re-ran the original failing test/command and it now passes
- [ ] Cited the source answer URL(s); fix saved to memory for recurrence
