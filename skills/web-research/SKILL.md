---
name: web-research
description: Live web search for current, real-time information the offline knowledge store can't have — recent releases, current pricing/docs, breaking changes, news, anything time-sensitive. Use when a question needs what's true RIGHT NOW rather than static engineering knowledge (that's expert-answer's job). Uses engine-ai's web_search MCP tool (Brave Search if a key is configured, DuckDuckGo otherwise — no key required).
---

# Web Research

Don't answer time-sensitive questions from memory — **search the live web**, then ground the
answer in what you found. This is `expert-answer`'s counterpart for anything that changes after a
model's training cutoff: current versions, pricing, recent incidents, breaking API changes, news.

## When to use
- "What's the latest version of …", "is X still true today", "current pricing for …", "did Y change
  recently", "what happened with …" — anything where staleness would make the answer wrong.
- NOT for stable engineering knowledge (architecture, algorithms, best practices) — that's
  `expert-answer`, grounded in the ingested knowledge store instead.
- Can complement `expert-answer`: ground the *stable* parts in ingested knowledge, and use
  `web_search` only for the parts that are genuinely time-sensitive.

## Method
1. **Check provider status** (optional but cheap) — `web_search_status()` tells you whether Brave
   Search (higher quality) or the DuckDuckGo fallback (no key needed) is active.
2. **Search** — call `web_search({ query, k })` with a specific, well-formed query (not the raw user
   question verbatim — extract the actual thing that needs a current answer). Returns
   `{ title, url, snippet }` hits.
3. **Synthesize, don't just relay** — read the snippets, reconcile conflicting sources, and give a
   direct answer. **Cite every claim with its URL.** If results are thin or conflicting, say so
   rather than guessing.
4. **Remember** — `memory_save([...topic keywords], answer-summary, { urls: [...] })` so a related
   follow-up in this session/app reuses the finding instead of re-searching.

## Red flags
- Answering a clearly time-sensitive question without calling `web_search` first.
- Presenting a single search snippet as settled fact with no citation or cross-check.
- Using `web_search` for stable engineering-design questions that `expert-answer` already covers
  better (with domain grounding + citations from the curated knowledge store).

## Verification
- [ ] Confirmed the question is genuinely time-sensitive (else routed to `expert-answer` instead)
- [ ] Called `web_search` with a specific, targeted query
- [ ] Answer cites source URLs; conflicting/thin results are stated honestly
- [ ] Reusable finding saved to memory
