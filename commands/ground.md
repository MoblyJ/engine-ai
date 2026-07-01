---
description: Index the current repo and answer/work grounded in its actual code (repo-aware RAG).
---

Ground your work in this repository before acting on: $ARGUMENTS

1. Call the MCP tool `index_repo` on the current working directory (absolute path).
2. Call `search_repo` with the key terms from the request to retrieve the most relevant code/docs.
3. Use the retrieved chunks (cite file paths) as the basis for your answer or change — do not guess
   at the codebase when you can search it.
