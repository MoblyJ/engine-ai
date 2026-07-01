"""Deterministic expert routing — map a request to the domain expert(s) to consult.

Complements the orchestrator's reasoning with a keyword router: given a request, score every domain
by trigger-word hits and return the ranked `domain-<slug>` experts (with whether each has ingested
knowledge). Used by `suggest_experts` and by /new-app + /expert for repeatable routing.
"""

from __future__ import annotations

import re

# domain slug -> trigger terms (substring match, lowercased). Keep aligned with gen_domain_agents.DOMAINS.
KEYWORDS = {
    "frontend": ["ui", "css", "react", "vue", "svelte", "angular", "tailwind", "frontend", "front-end",
                 "component", "accessibility", "a11y", "browser", "dom", "html", "webpack", "vite"],
    "backend": ["backend", "back-end", "api", "server", "endpoint", "service", "auth", "jwt", "orm",
                "crud", "middleware", "rest api", "web service"],
    "fullstack": ["fullstack", "full-stack", "end-to-end", "full stack"],
    "devops": ["devops", "ci/cd", "ci cd", "pipeline", "jenkins", "github actions", "gitlab ci",
               "automation", "ansible", "deployment", "release"],
    "cloud": ["cloud", "aws", "gcp", "azure", "serverless", "lambda", "s3", "ec2", "cloud run"],
    "kubernetes": ["kubernetes", "k8s", "kubectl", "helm", "pod", "operator", "kubelet"],
    "containers": ["docker", "container", "dockerfile", "compose", "image", "oci", "podman"],
    "system-design": ["scalability", "scalable", "distributed", "throughput", "load balancer",
                      "sharding", "partition", "cap theorem", "design a", "high availability", "at scale"],
    "distributed-systems": ["consensus", "raft", "paxos", "replication", "quorum", "eventual consistency",
                            "gossip", "leader election", "split brain"],
    "databases": ["database", "sql", "postgres", "mysql", "mongodb", "nosql", "index", "transaction",
                  "query", "schema", "migration", "acid", "normalization"],
    "caching": ["cache", "caching", "redis", "memcached", "cdn", "invalidation", "ttl"],
    "messaging": ["kafka", "queue", "rabbitmq", "pubsub", "pub/sub", "event-driven", "streaming",
                  "sqs", "sns", "message broker"],
    "networking": ["network", "http", "tcp", "dns", "tls", "ssl", "load balancing", "proxy", "gateway",
                   "grpc", "websocket"],
    "security": ["security", "authentication", "authorization", "owasp", "xss", "csrf", "sql injection",
                 "encryption", "crypto", "vulnerability", "threat", "oauth", "password", "secure"],
    "api-design": ["api design", "rest", "graphql", "grpc", "openapi", "swagger", "versioning", "webhook"],
    "microservices": ["microservice", "service mesh", "saga", "bounded context", "istio", "monolith"],
    "machine-learning": ["machine learning", "ml model", "feature engineering", "training data",
                         "dataset", "classifier", "regression", "sklearn", "scikit"],
    "deep-learning": ["deep learning", "neural network", "cnn", "rnn", "transformer", "pytorch",
                      "tensorflow", "backprop", "gradient descent"],
    "llm": ["llm", "gpt", "prompt", "rag", "retrieval", "embedding", "fine-tune", "fine tune", "agent",
            "langchain", "vector db", "vector database", "chatbot", "context window"],
    "prompt-engineering": ["prompt engineering", "few-shot", "chain-of-thought", "chain of thought",
                           "system prompt", "prompt template"],
    "ai": ["artificial intelligence", "recommendation", "computer vision", "nlp", "ai feature"],
    "data-engineering": ["etl", "data pipeline", "warehouse", "data lake", "spark", "airflow", "dbt",
                         "ingestion", "batch processing"],
    "mobile": ["mobile", "ios", "android", "react native", "flutter", "swift", "kotlin", "offline-first"],
    "testing": ["test", "testing", "unit test", "e2e", "tdd", "coverage", "jest", "pytest", "mock",
                "integration test"],
    "performance": ["performance", "latency", "optimize", "profiling", "web vitals", "bottleneck",
                    "throughput", "memory leak"],
    "observability": ["observability", "metrics", "logging", "tracing", "prometheus", "grafana", "slo",
                      "monitoring", "alerting"],
    "iac": ["terraform", "pulumi", "cloudformation", "infrastructure as code", "provisioning"],
    "architecture": ["architecture", "design pattern", "ddd", "domain-driven", "adr", "clean architecture",
                     "hexagonal", "boundaries", "modular"],
    "algorithms": ["algorithm", "data structure", "complexity", "big o", "sorting", "graph traversal",
                   "dynamic programming", "leetcode", "recursion"],
    "roadmaps": ["roadmap", "learn", "career", "skill path", "beginner", "study plan"],
}

DEFAULTS = ["fullstack", "architecture"]


def suggest(request: str, k: int = 3, knowledge=None) -> dict:
    """Return the ranked domain experts to consult for a request."""
    text = " " + re.sub(r"\s+", " ", (request or "").lower()) + " "
    scored = []
    for domain, terms in KEYWORDS.items():
        hits = [t for t in terms if t in text]
        if hits:
            scored.append((len(hits), domain, hits))
    scored.sort(key=lambda x: x[0], reverse=True)
    picks = [(d, h) for _, d, h in scored[:k]] or [(d, []) for d in DEFAULTS]

    have = {}
    if knowledge is not None:
        try:
            have = {d["domain"]: d["chunks"] for d in knowledge.domains()}
        except Exception:  # noqa: BLE001
            have = {}
    experts = [{
        "domain": d, "agent": f"domain-{d}", "command": "/expert",
        "matched": h, "has_knowledge": d in have, "chunks": have.get(d, 0),
    } for d, h in picks]
    return {"request": request, "experts": experts,
            "fallback": not scored}
