---
name: expert-answer
description: Answer engineering questions — design, architecture, best practices, "how should I…", tradeoffs, technology choices — grounded in engine-ai's ingested knowledge + domain experts, WITH CITATIONS. Use when the user asks how to design/architect/choose/scale/secure something or asks for best practices, rather than asking to build a full app. Covers frontend, backend, devops, cloud, system design, databases, security, ML, LLM, and more.
---

# Expert Answer

Don't answer engineering-design questions from memory alone — **ground them** in the ingested
knowledge and the domain experts, and **cite** what you use. This is what makes answers trustworthy
and repeatable.

## When to use
- "How should I design/architect/scale/secure …", "what's the best way to …", "X vs Y?", "best
  practices for …", "review this approach".
- NOT for "build me a full app" (that's `deployable-app` / `/new-app`) — though you can use this to
  inform such a build.

## Method
1. **Route** — call `suggest_experts({ request })` for the ranked `domain-<slug>` expert(s) and whether
   each has ingested knowledge.
2. **Ground** — call `context_pack({ keywords })` (prior memory + retrieved domain knowledge). For a
   deeper pull, `knowledge_search({ query, domain })`. For domain depth, **delegate to the matching
   `domain-<slug>` expert** (Task tool) — it retrieves + cites.
3. **Answer** — give a concrete, opinionated recommendation with tradeoffs. **Cite every sourced claim
   as `repo/path`.** If the knowledge store lacks coverage, say so and answer from first principles,
   and suggest `knowledge_ingest(<repo-url>, "<domain>")` to add it.
4. **Remember** — `memory_save([...domain, topic], answer-summary, data)` so the guidance evolves and
   the next related question builds on it.

## Red flags
- Confident architecture advice with **no citation** and no `suggest_experts`/`context_pack` call.
- Ignoring an available domain expert for a domain-heavy question.

## Verification
- [ ] Routed via `suggest_experts` (or a clearly-correct manual pick)
- [ ] Grounded via `context_pack` / `knowledge_search` / a domain expert
- [ ] Claims cite `repo/path`; gaps are stated honestly
- [ ] Reusable insight saved to memory
