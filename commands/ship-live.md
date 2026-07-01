---
description: Publish the current project to GitHub and deploy it live to Vercel, then verify the live URL.
---

Publish and deploy this project: ${ARGUMENTS:-.}

Follow the `publish-and-deploy` skill:
1. Confirm tests pass and `deploy_readiness` is 100 first (run `/deploy-check .` if unsure).
2. **Ask the user** for the GitHub repo name and whether it should be private or public.
3. Call `git_publish(path, repo_name, private)`. If it returns `needs_auth`, show the user the exact
   command from its `fix` field (`gh auth login`) and stop — do not work around it.
4. Call `vercel_deploy(path)` (add `prod=true` for production). If `needs_auth`, show `vercel login`.
5. Verify the returned live URL actually responds, then report the GitHub URL + the live Vercel URL.
