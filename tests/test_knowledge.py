"""Knowledge store — FTS5 ingest, domain-filtered search, translation skip, context_pack."""
import os
import sys
import tempfile
import unittest

os.environ["ENGINE_AI_HOME"] = tempfile.mkdtemp()  # isolate before import
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp"))
from knowledge import Knowledge, _match  # noqa: E402


def _w(root, rel, text):
    full = os.path.join(root, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    open(full, "w").write(text)


class TestMatch(unittest.TestCase):
    def test_builds_or_prefix_query(self):
        self.assertEqual(_match("Cache invalidation CDN"), "cache* OR invalidation* OR cdn*")
        self.assertEqual(_match("the a of"), "")  # all stopwords → empty
        self.assertEqual(_match("!!! ??"), "")     # no terms


class TestKnowledge(unittest.TestCase):
    def setUp(self):
        self.k = Knowledge(db_path=os.path.join(tempfile.mkdtemp(), "k.db"))
        self.repo = tempfile.mkdtemp()
        _w(self.repo, "README.md", "# System Design\nHorizontal scaling with a load balancer and cache invalidation via CDN.")
        _w(self.repo, "README-ja.md", "日本語 スケーリング キャッシュ")   # translated → must be skipped
        _w(self.repo, "src/lb.py", "def load_balancer(requests):\n    # round robin across backends\n    return requests")
        _w(self.repo, "assets/logo.png.md", "should be skipped (assets dir)")

    def test_ingest_skips_translations_and_assets(self):
        r = self.k.ingest_path(self.repo, "system-design", "demo")
        self.assertTrue(r["ok"])
        self.assertEqual(r["files"], 2)  # README.md + src/lb.py ; not the -ja or assets/
        d = {x["domain"]: x for x in self.k.domains()}
        self.assertIn("system-design", d)

    def test_search_ranks_english_and_filters_domain(self):
        self.k.ingest_path(self.repo, "system-design", "demo")
        hits = self.k.search("cache invalidation cdn", domain="system-design", k=3)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["path"], "README.md")   # English content, not the translation
        # code content is findable too
        self.assertTrue(self.k.search("round robin backends"))
        # wrong domain filter → nothing
        self.assertEqual(self.k.search("cache", domain="mobile"), [])

    def test_context_pack_includes_knowledge(self):
        self.k.ingest_path(self.repo, "system-design", "demo")
        ctx = self.k.context_pack(["scaling", "load-balancer"])
        self.assertIn("Domain knowledge", ctx)
        self.assertIn("demo", ctx)

    def test_search_empty_query_safe(self):
        self.k.ingest_path(self.repo, "system-design", "demo")
        self.assertEqual(self.k.search("!!!"), [])   # no valid terms → no crash

    def test_sources_recorded(self):
        self.k.ingest_path(self.repo, "system-design", "demo")
        src = {s["repo"]: s for s in self.k.sources()}
        self.assertIn("demo", src)
        self.assertEqual(src["demo"]["domain"], "system-design")


if __name__ == "__main__":
    unittest.main()
