"""Memory pockets — project scoping, immutable revisions, explicit supersede, previews."""
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

    def test_similar_keywords_do_not_merge(self):
        """v0.16: overlapping keywords NEVER auto-merge — records stay distinct+immutable."""
        r1 = self.m.save(["gamehub", "landing"], "v1: hero + free downloads.", {"v": 1})
        r2 = self.m.save(["gamehub", "landing", "mobile"], "v2: made it responsive.", {"v": 2})
        self.assertTrue(r2["ok"])
        self.assertNotEqual(r1["id"], r2["id"], "a new record must not clobber the old one")
        pockets = self.m.list()
        self.assertEqual(len(pockets), 2, "both pockets persist")
        rec = self.m.recall(["gamehub"], k=2)
        texts = {p["preview"] for p in rec["pockets"]}
        self.assertIn("v1: hero + free downloads.", texts)
        self.assertIn("v2: made it responsive.", texts)

    def test_explicit_supersede_retires_old_pocket(self):
        old = self.m.save(["auth", "login"], "Login uses JWT sessions.", {"algo": "HS256"}, kind="decision")
        new = self.m.save(["auth", "login"], "Login uses rotating refresh tokens.", {},
                          kind="decision", supersedes=old["id"])
        self.assertEqual(new["revision"], 2)
        rec = self.m.recall(["auth"])
        self.assertEqual([p["id"] for p in rec["pockets"]], [new["id"]],
                         "superseded pocket must not be recalled")
        got = self.m.get(old["id"])
        self.assertEqual(got["superseded_by"]["id"], new["id"])
        self.assertEqual(got["text"], "Login uses JWT sessions.", "old evidence stays immutable")
        chain = self.m.get(new["id"])["supersedes_chain"]
        self.assertEqual(len(chain), 1)

    def test_supersede_rejects_cross_project(self):
        a = self.m.save(["x"], "fact A", {}, project="proj-a")
        r = self.m.save(["x"], "fact B", {}, project="proj-b", supersedes=a["id"])
        self.assertFalse(r["ok"])

    def test_project_isolation(self):
        """Facts from one project are NEVER recalled for another (no cross-project leak)."""
        self.m.save(["billing", "stripe"], "Stripe checkout wired.", {}, project="app-billing")
        rec = self.m.recall(["billing", "stripe"], project="app-shop")
        self.assertEqual(rec["pockets"], [], "cross-project recall must be empty")
        rec_same = self.m.recall(["billing", "stripe"], project="app-billing")
        self.assertTrue(rec_same["pockets"])

    def test_duplicate_save_is_idempotent(self):
        r1 = self.m.save(["t"], "same text", {"a": 1})
        r2 = self.m.save(["t"], "same text", {"a": 1})
        self.assertTrue(r2["duplicate"])
        self.assertEqual(r1["id"], r2["id"])
        self.assertEqual(len(self.m.list()), 1)

    def test_kind_validation(self):
        r = self.m.save(["t"], "x", kind="vibe")
        self.assertFalse(r["ok"])
        self.assertIn("fact", r["error"])

    def test_recall_returns_bounded_preview(self):
        long_text = "x" * 5000
        self.m.save(["big"], long_text, {})
        p = self.m.recall(["big"])["pockets"][0]
        self.assertLessEqual(len(p["preview"]), 610)
        self.assertTrue(p["truncated"])
        full = self.m.get(p["id"])
        self.assertEqual(full["text"], long_text, "full text retrievable on demand")

    def test_related_edges_within_project(self):
        self.m.save(["auth", "login", "jwt"], "JWT login.", {}, project="p1")
        self.m.save(["auth", "signup"], "OAuth signup.", {}, project="p1")
        got = self.m.get(self.m.list("p1")[0]["id"])
        # whichever pocket list() returns first, both should be linked to each other
        ids = {p["id"] for p in self.m.list("p1")}
        related = {r["id"] for r in self.m.get(max(ids))["related"]}
        self.assertTrue(related & ids - {max(ids)})

    def test_unrelated_not_confused(self):
        self.m.save(["billing", "stripe"], "Stripe checkout.", {})
        self.m.save(["gamehub", "landing"], "Landing page.", {})
        rec = self.m.recall(["gamehub"], k=2)
        self.assertEqual(rec["pockets"][0]["keywords"][0], "gamehub")

    def test_recall_empty(self):
        self.assertEqual(self.m.recall(["nothing-here"])["pockets"], [])

    def test_forget_clears_supersede_links(self):
        a = self.m.save(["x"], "old", {})
        b = self.m.save(["x"], "new", supersedes=a["id"])
        self.m.forget(b["id"])
        revived = self.m.get(a["id"])
        self.assertFalse(revived.get("superseded_by"), "orphaned supersede link cleared")
        self.assertTrue(self.m.recall(["x"])["pockets"], "old pocket recallable again")


if __name__ == "__main__":
    unittest.main()
