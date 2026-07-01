---
description: Ask a domain expert (frontend, backend, system-design, ML, LLM, security, …) — grounded in ingested engineering knowledge + memory.
---

Route to the right domain expert for: $ARGUMENTS

### 1. Pick the domain(s)
From the request, choose the best domain(s): frontend · backend · fullstack · devops · cloud ·
kubernetes · containers · system-design · distributed-systems · databases · caching · messaging ·
networking · security · api-design · microservices · machine-learning · deep-learning · llm ·
prompt-engineering · ai · data-engineering · mobile · testing · performance · observability · iac ·
architecture · algorithms · roadmaps. Call `knowledge_domains` to see what's actually ingested.

### 2. Delegate to the expert subagent
Invoke the matching **`domain-<slug>`** subagent via the Task tool. It will `context_pack` +
`knowledge_search` to ground its answer in the curated repos and **cite the source repo + path**.

### 3. Multi-domain
If two domains apply (e.g. "a scalable ML serving API" → `machine-learning` + `system-design`),
run both experts and synthesize their answers.

### 4. Remember
Save any reusable insight with `memory_save([...domain...], context, data)` so it evolves.
