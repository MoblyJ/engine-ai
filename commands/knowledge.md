---
description: Browse & search the ingested engineering knowledge store — domains, sources, and full-text search with citations.
---

Browse the knowledge store for: ${ARGUMENTS:-（no query — show what's ingested）}

### If no query (browse mode)
Call `knowledge_domains`. Present a table of **domains** (chunks · repos) and the **sources** ingested,
then invite the user to search (`/knowledge <query>`) or add more (`knowledge_ingest <repo-url> <domain>`).
Example:
```
domain            chunks  repos   sources
system-design      1177     3     system-design-primer, system-design-101, awesome-system-design-resources
llm                1427     2     Prompt-Engineering-Guide, Awesome-LLM
machine-learning    353     2     awesome-machine-learning, ML-For-Beginners
…
(nothing yet? run:  engine-ai knowledge sync)
```

### If a query is given (search mode)
- Parse an optional `domain:<slug>` prefix (e.g. `/knowledge domain:system-design rate limiting`).
- Call `knowledge_search({ query, domain?, k: 8 })`. Show the top hits as a numbered list, each with
  **repo · path · score** and the snippet.
- To read a full source, open the underlying file: `~/.engine-ai/sources/<repo>/<path>` with Read.
- Offer to deep-dive (open a file) or route the question to the matching `domain-<slug>` expert
  (`/expert <query>`).

Keep it a browsing aid — cite `repo/path` for anything you surface so it's traceable.
