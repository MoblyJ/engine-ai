---
description: Live web research for current, real-time information — session-aware, with evolving memory (uses the web-research skill).
---

Research the current, live-web answer to: $ARGUMENTS

Run this **session-aware** so findings persist and evolve:

### 0. Recall memory first
- Call `memory_context({ keywords })` — pull any prior finding on this topic so you don't
  re-search what's already known (unless the prior finding could plausibly be stale).

### 1–2. Search → synthesize
1. Optionally call `web_search_status()` to see which provider is active (Brave vs DuckDuckGo).
2. Call `web_search({ query, k })` with a specific, targeted query — extract the actual
   time-sensitive question from the request rather than passing it through verbatim.
3. Synthesize the hits into a direct answer. **Cite every claim with its source URL.** State
   plainly if results are thin or conflicting — do not guess past what the sources actually say.

### 3. Save the finding to memory
- `memory_save({ keywords, context: "<synthesized answer>", data: { urls: [<source urls>] } })` —
  so a related follow-up reuses this instead of re-searching from scratch.

Not done until you searched the live web (not memory alone), cited sources, and saved the finding
to memory.
