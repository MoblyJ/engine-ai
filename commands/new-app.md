---
description: Scaffold and build a new deployable app end-to-end (uses the deployable-app skill + engine-ai MCP tools).
---

Build a new deployable application based on: $ARGUMENTS

Follow the `deployable-app` skill. Steps:
1. Decide the kind (`node-api`, `python-api`, or `static`) from the request; ask only if ambiguous.
2. Call the MCP tool `scaffold_app` with the target path and kind.
3. Implement the requested features, keeping the `/healthz` endpoint working.
4. Run the generated tests; add tests for new behavior.
5. Call `deploy_readiness` and fix every gap until the score is 100.
6. Store any required secrets with `set_secret` and document them in `.env.example`.
7. Build the Docker image, run it, and verify `/healthz` before declaring done.
