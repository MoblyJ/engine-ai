---
name: publish-and-deploy
description: After an app is built and tested, publish it to GitHub and deploy it live to Vercel, then verify the live URL. Use when the user wants to push to GitHub, create a repo, deploy, go live, or ship to production. Asks for the repo name. Uses the engine-ai MCP tools git_publish and vercel_deploy.
---

# Publish & Deploy

Take a tested app from local → a GitHub repo → a live Vercel URL, and verify it's actually up.

## Preconditions
- App builds, **tests pass**, and `deploy_readiness` scores 100 (see `deployable-app`).
- For a frontend/UI app, `mobile-responsive` has passed.
- **Auth (one-time, user does this):**
  - GitHub: `gh auth login` — there is **no** way to reuse the Google account connected to Claude;
    GitHub needs its own login (or a Personal Access Token with `repo` scope, stored via `set_secret`).
  - Vercel: `vercel login` (or a `VERCEL_TOKEN`, also storable via `set_secret`).

## Workflow

```
1 ASK NAME ──▶ 2 PUBLISH (GitHub) ──▶ 3 DEPLOY (Vercel) ──▶ 4 LIVE TEST
```

### 1. Ask for the repo name
**Always ask the user what to name the GitHub repo** (and public vs private) before publishing.
Do not invent a name.

### 2. Publish to GitHub
Call `git_publish(path, repo_name, private)`. If it returns `needs_auth`, **stop and show the user
the exact `fix` command** (`gh auth login`) — don't try to work around it. On success, report the
repo URL.

### 3. Deploy to Vercel
Call `vercel_deploy(path)` for a preview, or `vercel_deploy(path, prod=true)` for production. If it
returns `needs_auth`, show the `fix` (`vercel login`). Note: static sites and Next.js deploy as-is;
a raw Node API needs a serverless adapter first.

### 4. Live test
Fetch the returned Vercel URL and verify the real endpoints/pages respond (e.g. `curl <url>/healthz`,
or load the page). For a UI, run a quick `responsive_audit` thought-check on the deployed look.
Only call it "shipped" once the **live URL actually responds**.

## Red flags
- Publishing without asking the repo name · committing secrets (use `.env` + `set_secret`) ·
  claiming "deployed" without hitting the live URL · bypassing an auth prompt.

## Verification
- [ ] GitHub repo created and pushed (URL reported)
- [ ] Vercel deployment succeeded (live URL reported)
- [ ] The live URL responds (endpoint/page verified)
- [ ] No secrets committed
