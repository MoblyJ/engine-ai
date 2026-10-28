"""Deterministic expert routing — map a request to the domain expert(s) to consult.

Complements the orchestrator's reasoning with a keyword router: given a request, score every
domain by trigger-phrase hits and return the ranked `domain-<slug>` experts (with whether each
has ingested knowledge). Used by `suggest_experts` and by /new-app + /expert for repeatable
routing.

v0.16 (per the upgrade review):
- **Phrase/token matching, not substring**: terms match at word boundaries (optionally
  pluralized), so `ui` no longer matches `build`, `auth` no longer matches `authentication`,
  and multi-word phrases like `rest api` / `ci cd` match with flexible separators.
- **Limited selection**: k defaults to 3 and is hard-capped at 5 — no summoning the whole swarm.
- **Typed task profile**: the request is classified (design / implement / review / research)
  and each domain maps to an engine_core worker profile, so routing answers "which profile
  should run this", not just "which domain knows about it".
"""

from __future__ import annotations

import re

# domain slug -> trigger terms. Multi-word entries match as phrases; single words match
# as word-initial stems (term → term/terms). Keep aligned with gen_domain_agents.DOMAINS.
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
    "caching": ["cache", "redis", "memcached", "cdn", "invalidation", "ttl"],
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
MAX_EXPERTS = 5

# request-intent verbs -> typed task profile (see engine_core profiles)
PROFILES = {
    "design": ("design", "architect", "plan", "model", "schema", "roadmap", "structure"),
    "review": ("review", "audit", "security", "check", "risk", "threat"),
    "research": ("research", "compare", "evaluate", "investigate", "current", "latest", "pricing"),
    "implement": ("build", "implement", "add", "fix", "create", "write", "refactor", "ship", "deploy",
                  "test", "migrate", "integrate"),
}

# domain -> engine_core worker profile that typically executes its work
DOMAIN_PROFILE = {
    "frontend": "frontend", "backend": "backend", "fullstack": "backend", "api-design": "backend",
    "databases": "database", "data-engineering": "database", "caching": "services",
    "messaging": "services", "microservices": "services", "devops": "services",
    "cloud": "services", "kubernetes": "services", "containers": "services", "iac": "services",
    "testing": "test-author", "security": "security-review", "architecture": "reviewer",
    "system-design": "planner", "distributed-systems": "planner", "algorithms": "planner",
    "roadmaps": "planner", "performance": "reviewer", "observability": "reviewer",
    "machine-learning": "researcher", "deep-learning": "researcher", "llm": "researcher",
    "prompt-engineering": "researcher", "ai": "researcher", "mobile": "frontend",
    "networking": "backend",
}


def _term_re(term: str) -> re.Pattern:
    """Word-boundary, plural-tolerant pattern for a trigger term.

    `embedding` matches `embeddings`; `auth` does NOT match `authentication`;
    `rest api` matches `rest api`, `rest-api`, `rest  api`.
    """
    words = term.split()
    parts = [re.escape(w) for w in words]
    parts[-1] += r"\w*"  # tolerate plurals/short suffixes on the final word
    sep = r"[\s_/-]+"  # space, underscore, dash, slash between the phrase's words
    return re.compile(r"(?<![\w])" + sep.join(parts) + r"(?![\w])", re.I)


_RES = {domain: [(t, _term_re(t), 2 if " " in t else 1) for t in terms]
        for domain, terms in KEYWORDS.items()}


def task_profile(request: str) -> str:
    text = (request or "").lower()
    best, best_hits = "implement", 0
    for profile, verbs in PROFILES.items():
        hits = sum(1 for v in verbs if re.search(rf"(?<![\w]){re.escape(v)}(?![\w])", text))
        if hits > best_hits:
            best, best_hits = profile, hits
    return best


def suggest(request: str, k: int = 3, knowledge=None) -> dict:
    """Return the ranked domain experts to consult for a request."""
    text = (request or "").strip()
    k = max(1, min(k, MAX_EXPERTS))
    scored = []
    for domain, terms in _RES.items():
        hits = [t for t, rx, w in terms if rx.search(text)]
        if hits:
            weight = sum(2 if " " in h else 1 for h in hits)
            scored.append((weight, len(hits), domain, hits))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    picks = [(d, h) for _, _, d, h in scored[:k]] or [(d, []) for d in DEFAULTS]

    have = {}
    if knowledge is not None:
        try:
            have = {d["domain"]: d["chunks"] for d in knowledge.domains()}
        except Exception:  # noqa: BLE001
            have = {}
    profile = task_profile(request)
    experts = [{
        "domain": d, "agent": f"domain-{d}", "command": "/expert",
        "matched": h, "match_weight": sum(2 if " " in x else 1 for x in h),
        "has_knowledge": d in have, "chunks": have.get(d, 0),
        "worker_profile": DOMAIN_PROFILE.get(d, "backend"),
    } for d, h in picks]
    return {"request": request, "experts": experts,
            "task_profile": profile,
            "fallback": not scored}
