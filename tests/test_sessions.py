"""App sessions — git worktree per app, list with 2-line summary, resume with memory."""
import os
import subprocess
import sys
import tempfile
import unittest

os.environ["ENGINE_AI_HOME"] = tempfile.mkdtemp()  # isolate before importing
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp"))
from sessions import Sessions, _slug  # noqa: E402

HAS_GIT = subprocess.run(["bash", "-c", "command -v git"], capture_output=True).returncode == 0


class TestSlug(unittest.TestCase):
    def test_slug(self):
        self.assertEqual(_slug("Free Gaming Landing Page!"), "free-gaming-landing-page")
        self.assertEqual(_slug(""), "app")


class TestSessions(unittest.TestCase):
    def setUp(self):
        self.s = Sessions(db_path=os.path.join(tempfile.mkdtemp(), "s.db"))

    @unittest.skipUnless(HAS_GIT, "git not available")
    def test_create_worktree(self):
        r = self.s.create("Gaming Landing", keywords=["gaming", "landing"])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["mode"], "worktree")
        self.assertTrue(os.path.isdir(r["path"]))
        # it's a real git worktree (has a .git file pointing at the central repo)
        self.assertTrue(os.path.exists(os.path.join(r["path"], ".git")))
        code = subprocess.run(["git", "-C", r["path"], "rev-parse", "--abbrev-ref", "HEAD"],
                              capture_output=True, text=True)
        self.assertEqual(code.stdout.strip(), "app/gaming-landing")

    @unittest.skipUnless(HAS_GIT, "git not available")
    def test_two_apps_isolated(self):
        a = self.s.create("App One", keywords=["one"])
        b = self.s.create("App Two", keywords=["two"])
        self.assertNotEqual(a["path"], b["path"])
        self.assertNotEqual(a["branch"], b["branch"])
        # writing in one doesn't touch the other
        open(os.path.join(a["path"], "a.txt"), "w").write("A")
        self.assertFalse(os.path.exists(os.path.join(b["path"], "a.txt")))

    @unittest.skipUnless(HAS_GIT, "git not available")
    def test_unique_slug(self):
        a = self.s.create("Dupe")
        b = self.s.create("Dupe")
        self.assertNotEqual(a["slug"], b["slug"])

    @unittest.skipUnless(HAS_GIT, "git not available")
    def test_list_two_line_summary(self):
        r = self.s.create("Time API", keywords=["api", "time"])
        self.s.update(r["id"], summary="Node API with GET /time + /healthz.", keywords=["api", "time", "node"])
        items = self.s.list()
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertIn("Time API", it["line1"])
        self.assertIn("#" + str(r["id"]), it["line1"])
        self.assertIn("/time", it["line2"])

    @unittest.skipUnless(HAS_GIT, "git not available")
    def test_resume_returns_path_and_memory(self):
        # save a memory pocket under the app's keywords first
        from memory import Memory
        Memory().save(["billing", "stripe"], "Stripe checkout wired.", {"provider": "stripe"})
        r = self.s.create("Billing App", keywords=["billing", "stripe"])
        res = self.s.resume(r["slug"])
        self.assertTrue(res["ok"])
        self.assertEqual(res["path"], r["path"])
        self.assertTrue(res["exists"])
        self.assertIn("Stripe checkout", res["memory_context"])  # memory restored on resume

    def test_resume_missing(self):
        self.assertFalse(self.s.resume("nope-404")["ok"])

    @unittest.skipUnless(HAS_GIT, "git not available")
    def test_find_by_cwd(self):
        r = self.s.create("Shop App", keywords=["shop"])
        # a working dir inside the worktree resolves to the session
        inner = os.path.join(r["path"], "src")
        os.makedirs(inner, exist_ok=True)
        found = self.s.find(inner)
        self.assertTrue(found["ok"])
        self.assertEqual(found["id"], r["id"])
        self.assertEqual(found["branch"], r["branch"])
        # an unrelated dir is not matched
        self.assertFalse(self.s.find(tempfile.mkdtemp())["ok"])


if __name__ == "__main__":
    unittest.main()
