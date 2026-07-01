#!/usr/bin/env python3
"""Sync the curated engineering-knowledge repos into the domain-tagged store.

Run: python3 mcp/knowledge_sync.py         (all)   ·   ... <domain>   (one domain)
Resilient: shallow-clones each repo, ingests text/docs/code only (images/binaries skipped),
continues on per-repo failure. This is the "download + master a domain by retrieval" step —
no model training, no multi-TB corpora (The Stack / full HF hub are intentionally excluded).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from knowledge import Knowledge  # noqa: E402

# (git URL, domain) — curated, markdown/code-dense, practical engineering knowledge.
CURATED = [
    ("https://github.com/donnemartin/system-design-primer.git", "system-design"),
    ("https://github.com/ashishps1/awesome-system-design-resources.git", "system-design"),
    ("https://github.com/ByteByteGoHq/system-design-101.git", "system-design"),
    ("https://github.com/josephmisiti/awesome-machine-learning.git", "machine-learning"),
    ("https://github.com/dair-ai/Prompt-Engineering-Guide.git", "llm"),
    ("https://github.com/Hannibal046/Awesome-LLM.git", "llm"),
    ("https://github.com/codecrafters-io/build-your-own-x.git", "systems"),
    ("https://github.com/microsoft/AI-For-Beginners.git", "ai"),
    ("https://github.com/microsoft/ML-For-Beginners.git", "machine-learning"),
    ("https://github.com/karpathy/nn-zero-to-hero.git", "deep-learning"),
    ("https://github.com/kamranahmedse/developer-roadmap.git", "roadmaps"),
    # --- coverage for the domains that had experts but thin data ---
    ("https://github.com/goldbergyoni/nodebestpractices.git", "backend"),
    ("https://github.com/thedaviddias/Front-End-Checklist.git", "frontend"),
    ("https://github.com/leonardomso/33-js-concepts.git", "frontend"),
    ("https://github.com/bregman-arie/devops-exercises.git", "devops"),
    ("https://github.com/open-guides/og-aws.git", "cloud"),
    ("https://github.com/kelseyhightower/kubernetes-the-hard-way.git", "kubernetes"),
    ("https://github.com/OWASP/CheatSheetSeries.git", "security"),
    ("https://github.com/microsoft/api-guidelines.git", "api-design"),
    ("https://github.com/goldbergyoni/javascript-testing-best-practices.git", "testing"),
    ("https://github.com/thedaviddias/Front-End-Performance-Checklist.git", "performance"),
    ("https://github.com/keyvanakbary/learning-notes.git", "architecture"),
    ("https://github.com/andkret/Cookbook.git", "data-engineering"),
    ("https://github.com/dastergon/awesome-sre.git", "observability"),
    ("https://github.com/mfornos/awesome-microservices.git", "microservices"),
    ("https://github.com/shuaibiyy/awesome-terraform.git", "iac"),
    ("https://github.com/trekhleb/javascript-algorithms.git", "algorithms"),
    ("https://github.com/numetriclabz/awesome-db.git", "databases"),
]


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    kn = Knowledge()
    total_files = total_chunks = ok = 0
    for url, domain in CURATED:
        if only and domain != only:
            continue
        name = url.rstrip("/").split("/")[-1].removesuffix(".git")
        print(f"→ [{domain}] {name} …", flush=True)
        try:
            r = kn.ingest_repo(url, domain)
        except Exception as e:  # noqa: BLE001
            print(f"   ✗ {e}", flush=True); continue
        if r.get("ok"):
            ok += 1; total_files += r["files"]; total_chunks += r["chunks"]
            print(f"   ✓ {r['files']} files, {r['chunks']} chunks", flush=True)
        else:
            print(f"   ✗ {r.get('error')}: {str(r.get('detail',''))[:120]}", flush=True)
    print(f"\n✓ synced {ok} repos · {total_files} files · {total_chunks} chunks", flush=True)
    print("domains:", {d["domain"]: d["chunks"] for d in kn.domains()}, flush=True)


if __name__ == "__main__":
    main()
