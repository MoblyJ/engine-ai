---
description: Audit deployability and fix the gaps — session-aware, with evolving memory.
---

Audit this project for deployability: ${ARGUMENTS:-.}

Run this **session-aware** so deploy state persists and evolves:

### 0. Locate the app + recall memory
- Call `app_find({ path: <cwd or $ARGUMENTS> })`. If it matches an app session, use its `path` and
  `keywords`; otherwise use the given path and derive keywords from it.
- Call `memory_context({ keywords: [...,"deploy","readiness"] })` — reuse any prior deploy decisions
  (what was already added/justified) instead of re-deciding.

### 1–3. Audit → fix → re-audit
1. Call `deploy_readiness(path)`.
2. Report the score, the failing checks, and the recommendations as a checklist.
3. Offer to fix each gap (Dockerfile, tests, CI, `.env.example`, README, healthcheck). On agreement,
   implement them — use `scaffold_app` for templates where helpful — and re-run `deploy_readiness`
   until the score is 100.

### 4. Save back to the session + memory
- `memory_save({ keywords: [...,"deploy"], context: "<final score + what was fixed/justified>", data: { "deploy_readiness": <n> } })`.
- If an app session matched, `app_update({ id, summary })` — append the deploy status (e.g. "🚀 deploy-ready ✓ 100") to its 2-line summary so `/resume-app` shows it.

Not done until the audit ran, gaps were fixed (or justified), and the result was saved to memory
(+ the session, if any).
