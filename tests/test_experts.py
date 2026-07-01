"""Deterministic expert routing — suggest_experts."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp"))
from experts import suggest  # noqa: E402


class _FakeKnowledge:
    def domains(self):
        return [{"domain": "security", "chunks": 2204}, {"domain": "frontend", "chunks": 6222}]


class TestSuggest(unittest.TestCase):
    def _top(self, req, k=3):
        return [e["domain"] for e in suggest(req, k=k)["experts"]]

    def test_routes_to_expected_domains(self):
        self.assertIn("security", self._top("secure user authentication with jwt and oauth"))
        self.assertIn("frontend", self._top("build a react UI with tailwind and accessibility"))
        self.assertIn("system-design", self._top("design a scalable distributed rate limiter"))
        self.assertIn("llm", self._top("build a RAG pipeline with embeddings and a vector db"))

    def test_ranks_by_hit_count(self):
        # a caching-heavy request should surface caching high
        top = self._top("redis cache invalidation with a cdn and ttl", k=2)
        self.assertIn("caching", top)

    def test_agent_and_command_fields(self):
        e = suggest("kubernetes helm operator")["experts"][0]
        self.assertEqual(e["agent"], "domain-kubernetes")
        self.assertEqual(e["command"], "/expert")

    def test_fallback_when_no_match(self):
        r = suggest("zzzz qqqq wwww")
        self.assertTrue(r["fallback"])
        self.assertEqual([e["domain"] for e in r["experts"]], ["fullstack", "architecture"])

    def test_has_knowledge_flag(self):
        r = suggest("secure authentication", knowledge=_FakeKnowledge())
        sec = next(e for e in r["experts"] if e["domain"] == "security")
        self.assertTrue(sec["has_knowledge"])
        self.assertEqual(sec["chunks"], 2204)


if __name__ == "__main__":
    unittest.main()
