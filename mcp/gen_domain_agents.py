#!/usr/bin/env python3
"""Generate the domain-expert subagent swarm into ../agents/domain-*.md.

Each domain agent "masters" its field by RETRIEVAL from engine-ai's knowledge store (curated repos)
plus evolving memory — not by training. Regenerate any time: python3 mcp/gen_domain_agents.py
"""

from __future__ import annotations

import os

AGENTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agents")

# (slug, title, focus)
DOMAINS = [
    ("frontend", "Frontend Engineering", "UI, React/Vue/Svelte, CSS, accessibility, and web performance"),
    ("backend", "Backend Engineering", "APIs, services, data access, auth, and scaling"),
    ("fullstack", "Full-Stack Engineering", "end-to-end web apps, front to back"),
    ("devops", "DevOps", "CI/CD, automation, reliability, and delivery"),
    ("cloud", "Cloud Architecture", "AWS/GCP/Azure, serverless, and cost/scaling"),
    ("kubernetes", "Kubernetes", "orchestration, deployments, operators, and scaling"),
    ("containers", "Containers & Docker", "images, compose, and runtime"),
    ("system-design", "System Design", "scalability, distributed systems, and tradeoffs"),
    ("distributed-systems", "Distributed Systems", "consensus, replication, partitioning, and consistency"),
    ("databases", "Databases", "SQL/NoSQL, indexing, transactions, and data modeling"),
    ("caching", "Caching", "Redis, CDNs, and invalidation strategies"),
    ("messaging", "Messaging & Streaming", "Kafka, queues, and event-driven design"),
    ("networking", "Networking", "HTTP, TCP, DNS, and load balancing"),
    ("security", "Security", "OWASP, authn/authz, crypto, and threat modeling"),
    ("api-design", "API Design", "REST, GraphQL, gRPC, versioning, and OpenAPI"),
    ("microservices", "Microservices", "service boundaries, sagas, and service mesh"),
    ("machine-learning", "Machine Learning", "models, features, evaluation, and pipelines"),
    ("deep-learning", "Deep Learning", "neural networks, training, and architectures"),
    ("llm", "LLM Engineering", "prompting, RAG, agents, evals, and fine-tuning"),
    ("prompt-engineering", "Prompt Engineering", "prompt patterns, techniques, and evaluation"),
    ("ai", "Applied AI", "AI product patterns and integration"),
    ("data-engineering", "Data Engineering", "pipelines, ETL, warehouses, and lakes"),
    ("mobile", "Mobile Engineering", "iOS/Android, cross-platform, and offline-first"),
    ("testing", "Testing & QA", "unit/integration/e2e, TDD, and coverage"),
    ("performance", "Performance", "profiling, latency, throughput, and web vitals"),
    ("observability", "Observability", "metrics, logs, traces, and SLOs"),
    ("iac", "Infrastructure as Code", "Terraform, Pulumi, and provisioning"),
    ("architecture", "Software Architecture", "patterns, ADRs, boundaries, and DDD"),
    ("algorithms", "Algorithms & Data Structures", "complexity, data structures, and problem solving"),
    ("roadmaps", "Learning Roadmaps", "skill paths across engineering stacks"),
]

TEMPLATE = """---
name: domain-{slug}
description: {title} expert — answers, designs, and reviews {focus}, grounded in engine-ai's ingested engineering knowledge (retrieval, not training) plus live web search for anything time-sensitive. Use for {slug} questions, architecture, or building {slug} features.
tools: mcp__engine-ai__context_pack, mcp__engine-ai__knowledge_search, mcp__engine-ai__knowledge_domains, mcp__engine-ai__web_search, mcp__engine-ai__memory_context, mcp__engine-ai__memory_recall, mcp__engine-ai__memory_save, Read
---

# {title} Expert

You are a {title} specialist. You **master this domain by RETRIEVAL** from engine-ai's knowledge store
(curated engineering repos) plus evolving memory — not by trained weights. Always ground your answer.

## Method
1. Call `context_pack({{ keywords: ["{slug}", ...task terms] }})` to assemble prior memory +
   retrieved domain knowledge. For a deeper dive, `knowledge_search({{ query, domain: "{slug}" }})`
   (or without `domain` to search all ingested repos).
2. If the question is time-sensitive (current versions, recent changes, pricing — not stable
   engineering knowledge), also call `web_search({{ query }})` and cite source URLs for that part.
3. Answer / design / review using the retrieved material — **cite the source repo + path** for claims.
4. If you learned something reusable, `memory_save(["{slug}", ...], context, data)` so it evolves.

## Scope
{focus}. Be concrete and practical. If the knowledge store lacks coverage, say so, answer from first
principles (or a `web_search` if it's time-sensitive), and suggest `knowledge_ingest(<repo-url>,
"{slug}")` to add durable coverage.
"""


def main():
    os.makedirs(AGENTS_DIR, exist_ok=True)
    n = 0
    for slug, title, focus in DOMAINS:
        with open(os.path.join(AGENTS_DIR, f"domain-{slug}.md"), "w") as f:
            f.write(TEMPLATE.format(slug=slug, title=title, focus=focus))
        n += 1
    print(f"generated {n} domain-expert agents into {AGENTS_DIR}")


if __name__ == "__main__":
    main()
