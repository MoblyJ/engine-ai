---
description: Publish the current app's worktree to GitHub and deploy it live to Vercel — session-aware, records the live URL back into the app session.
---

Publish and deploy this app: ${ARGUMENTS:-.}

Follow the `publish-and-deploy` skill, **session-aware**:

### 0. Identify the app session
- Call `app_find({ path: <cwd> })`. If it matches an app session, you're shipping **that app's
  worktree + branch** (`app/<slug>`) — use its `path` for all steps below and its `keywords` for
  memory. If it returns not-found, ship the given path as a standalone project.

### 1. Gate
Confirm tests pass and `deploy_readiness(path)` is 100 (run `/deploy-check` if unsure).

### 2. Publish to GitHub
- **Ask the user** for the repo name (default: the app slug) and public/private.
- `git_publish(path, repo_name, private)`. If it returns `needs_auth`, show the exact `fix`
  (`gh auth login`) and stop — never bypass it.

### 3. Deploy to Vercel
- `vercel_deploy(path)` (add `prod=true` for production). If `needs_auth`, show `vercel login`.
- Verify the returned live URL actually responds.

### 4. Record it back into the session
- `app_update({ id, summary })` — append the GitHub + live URLs to the app's 2-line summary so
  `/resume-app` shows it's shipped.
- `memory_save({ keywords, context, data })` — save the repo URL + live URL so future prompts know
  this app is deployed.

### 5. Report
The GitHub repo URL + the live Vercel URL, and that the session was updated.
