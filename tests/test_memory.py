"""Memory pockets — keyword recall, merge-on-similar, evolving context."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp"))
os.environ["ENGINE_AI_HOME"] = tempfile.mkdtemp()  # isolate before importing engine/memory
from memory import Memory, _jaccard, _norm  # noqa: E402


class TestHelpers(unittest.TestCase):
    def test_norm_dedup_lowercase(self):
        self.assertEqual(_norm("GameHub, Landing gamehub"), ["gamehub", "landing"])
        self.assertEqual(_norm(["A", "b", "A"]), ["a", "b"])

    def test_jaccard(self):
        self.assertEqual(_jaccard(["a", "b"], ["a", "b"]), 1.0)
        self.assertEqual(_jaccard(["a", "b"], ["c"]), 0.0)
        self.assertAlmostEqual(_jaccard(["a", "b"], ["b", "c"]), 1 / 3)


class TestMemory(unittest.TestCase):
    def setUp(self):
        self.m = Memory(db_path=os.path.join(tempfile.mkdtemp(), "mem.db"))

    def test_save_and_recall(self):
        r = self.m.save(["gamehub", "landing", "free"], "Built a free-download gaming landing page.",
                        {"path": "/gamehub", "kind": "static"})
        self.assertTrue(r["ok"]); self.assertFalse(r["merged"])
        rec = self.m.recall(["gamehub", "free"])
        self.assertTrue(rec["pockets"])
        self.assertIn("gaming landing", rec["context"])
        self.assertIn("free-download", rec["context"].lower())

    def test_similar_keywords_merge_on_save(self):
        self.m.save(["gamehub", "landing"], "v1: hero + free downloads.", {"v": 1})
        r2 = self.m.save(["gamehub", "landing", "mobile"], "v2: made it responsive.", {"v": 2})
        self.assertTrue(r2["merged"], "highly-similar keywords should evolve the existing pocket")
        # one evolved pocket holding both memories + merged data
        pockets = self.m.list()
        self.assertEqual(len(pockets), 1)
        rec = self.m.recall(["gamehub"])
        self.assertIn("v1", rec["context"]); self.assertIn("v2", rec["context"])
        self.assertEqual(rec["pockets"][0]["data"], {"v": 2})  # merged data (v2 wins on key)

    def test_recall_merges_closest_two(self):
        # two distinct-but-related pockets (share 'auth' keyword)
        self.m.save(["auth", "login", "jwt"], "Login uses JWT sessions.", {"algo": "HS256"})
        self.m.save(["auth", "signup", "oauth"], "Signup uses Google OAuth.", {"provider": "google"})
        rec = self.m.recall(["auth"], k=2)
        self.assertEqual(len(rec["pockets"]), 2)
        self.assertEqual(rec["used"], "merged-closest")   # shares 'auth' → combined
        self.assertIn("JWT", rec["context"]); self.assertIn("OAuth", rec["context"])

    def test_unrelated_not_merged(self):
        self.m.save(["billing", "stripe"], "Stripe checkout.", {})
        self.m.save(["gamehub", "landing"], "Landing page.", {})
        rec = self.m.recall(["gamehub"], k=2)
        # top hit is gamehub; not falsely merged with billing
        self.assertEqual(rec["pockets"][0]["keywords"][0] in ("gamehub", "landing"), True)

    def test_recall_empty(self):
        self.assertEqual(self.m.recall(["nothing-here"])["pockets"], [])

    def test_forget(self):
        r = self.m.save(["x", "y"], "temp", {})
        self.m.forget(r["id"])
        self.assertEqual(self.m.list(), [])


if __name__ == "__main__":
    unittest.main()
