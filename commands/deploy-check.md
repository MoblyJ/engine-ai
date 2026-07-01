---
description: Audit the current project for deployability and fix the gaps.
---

Audit this project for deployability: ${ARGUMENTS:-.}

1. Call the MCP tool `deploy_readiness` on the path.
2. Report the score, the failing checks, and the recommendations as a checklist.
3. Offer to fix each gap (Dockerfile, tests, CI, `.env.example`, README, healthcheck). If the user
   agrees, implement them — use `scaffold_app` for templates where helpful — and re-run
   `deploy_readiness` to confirm the score improved.
