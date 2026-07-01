---
name: deployable-app
description: Build a complete, deployable application end to end — scaffold, implement, test, containerize, and ship. Use when the user asks to build/create an app, service, API, or site, or to make an existing project deployable. Leans on the engine-ai MCP tools (scaffold_app, deploy_readiness, search_repo, set_secret).
---

# Deployable App

Build apps that actually ship — not demos. "Done" means: runs, tested, containerized, has CI,
documents its env, and has a healthcheck. Use the `engine-ai` MCP tools at each step.

## When to use
- "Build me a ___ app / API / service / landing page"
- "Make this project deployable / production-ready"
- NOT for one-off scripts or pure questions.

## Workflow

```
0 RECALL ─▶ 1 SCAFFOLD ─▶ 2 IMPLEMENT ─▶ 3 TEST ─▶ 4 READINESS ─▶ 5 MOBILE ─▶ 6 SECRETS ─▶ 7 PUBLISH ─▶ 8 DEPLOY ─▶ 9 SAVE
 memory_       scaffold_app   search_repo   tests   deploy_readiness  responsive_  set_secret  git_publish  vercel_deploy   memory_
 context                                                              audit        (GitHub)    +live test                   save
```
**Memory is automatic** — always start with step 0 (recall) and end with step 9 (save) so apps evolve
across prompts. For a frontend/UI app, run the **`mobile-responsive`** skill at step 5. For publishing + going live,
run the **`publish-and-deploy`** skill at steps 7–8 (it asks for the repo name and needs `gh auth
login` / `vercel login`).

### 0. Recall memory (automatic — always first)
Extract 3–6 **keywords** from the request (domain + tech + product name). Call `memory_context(keywords)`.
If it returns prior context, **build on it** — reuse the earlier paths, decisions, and structure so the
app *evolves* instead of starting over. If empty, it's a fresh build.

### 1. Scaffold
Pick a kind and call the MCP tool `scaffold_app(path, kind)` with `node-api`, `python-api`, or
`static`. It writes a skeleton that already has a `/healthz` endpoint, a test, a Dockerfile, a CI
workflow, `.env.example`, and `.gitignore`. For other stacks, scaffold the closest kind and adapt.

### 2. Implement
Add the real features. Before editing an existing/large repo, call `index_repo(path)` then
`search_repo(path, query)` to ground changes in the actual code. Keep the healthcheck working.

### 3. Test
Run the generated tests (`npm test` / `python -m unittest`). Add a test for every new behavior —
deployable means verifiable. Do not proceed with failing tests.

### 4. Readiness gate
Call `deploy_readiness(path)`. Resolve every `false` check and every recommendation before shipping
(Dockerfile, tests, CI, env template, README). Aim for score 100.

### 5. Secrets
Never hardcode keys. Store them with `set_secret(name, value)` and reference them via env
(`.env.example` documents which are required). Confirm with `list_secrets` (names only).

### 5b. Mobile (frontend apps)
If the app has a UI, follow the `mobile-responsive` skill: `responsive_audit(path)`, fix gaps, verify
at phone/tablet viewports.

### 6. Ship (container)
Build the image (`docker build`), run it, curl `/healthz`. Only claim "runs" after the container
answers ok.

### 7–8. Publish & go live
Follow the `publish-and-deploy` skill: **ask the user for the GitHub repo name**, `git_publish(...)`
to create + push the repo, then `vercel_deploy(...)` for a live URL, and verify the live URL responds.
If either returns `needs_auth`, show the user the exact login command (`gh auth login` / `vercel
login`) and stop — never bypass it.

### 9. Save memory (automatic — always last)
After the build succeeds, call `memory_save(keywords, context, data)` with the same keywords from step
0, a short summary of what you built + key decisions, and structured `data` to reuse (path, kind,
endpoints, stack). Similar keywords evolve the existing pocket. The task is not done until steps 0 and
9 have both run.

## Red flags
- Hardcoded secrets, no Dockerfile, no tests, no healthcheck → not deployable.
- Skipping the memory bookends (step 0 recall / step 9 save) — apps then can't evolve across prompts.
- "It works on my machine" with no container or CI.
- Claiming success without running the build or the tests.

## Verification
- [ ] `deploy_readiness` score is 100 (or every gap is justified)
- [ ] Tests pass
- [ ] `docker build` succeeds and the container answers `/healthz`
- [ ] Required env vars are in `.env.example`; no secrets committed
- [ ] README documents run + deploy
- [ ] **Memory recalled at step 0** and **saved at step 9** (so the next related build evolves from this)
