---
description: Scaffold and build a new deployable app end-to-end, with automatic evolving memory (uses the deployable-app skill + engine-ai MCP tools).
---

Build a new deployable application based on: $ARGUMENTS

Follow the `deployable-app` skill. **Memory is automatic — do the memory bookends every time:**

### Step 0 — RECALL memory (always, before anything else)
- Extract 3–6 **keywords** from the request (domain + tech + product name, e.g. for "a free gaming
  landing page" → `["gaming","landing","free-download","frontend"]`).
- Call `memory_context(keywords)`. If it returns context, **build on it** — reuse prior file paths,
  decisions, and structure so the app EVOLVES from earlier prompts instead of starting over. If it's
  empty, it's a fresh build.

### Steps 1–7 — build (deployable-app skill)
1. Decide the kind (`node-api`, `python-api`, or `static`); ask only if ambiguous.
2. `scaffold_app` with the target path and kind.
3. Implement the requested features, keeping `/healthz` working.
4. Run the generated tests; add tests for new behavior.
5. `deploy_readiness` → fix every gap until score 100.
6. If it has a UI, follow `mobile-responsive` (`responsive_audit`).
7. Store secrets with `set_secret`; document them in `.env.example`.

### Step 8 — SAVE memory (always, after the build succeeds)
- Call `memory_save` with:
  - `keywords`: the same keywords from Step 0 (so related builds cluster and evolve).
  - `context`: what you built and the key decisions (kind, features, structure).
  - `data`: structured facts to reuse next time — e.g. `{"path": "...", "kind": "...", "endpoints": [...], "stack": "..."}`.
- This makes the next `/new-app` on a related idea continue from here.

Do not consider the task done until Step 0 and Step 8 have both run.
