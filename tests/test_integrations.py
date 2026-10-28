"""Tests for the GitHub / Vercel / mobile-responsive integrations."""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp"))
import integrations as I  # noqa: E402

HAS_GIT = subprocess.run(["bash", "-c", "command -v git"], capture_output=True).returncode == 0


def _write(root, rel, content):
    full = os.path.join(root, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    open(full, "w").write(content)


class TestResponsiveAudit(unittest.TestCase):
    def test_responsive_site_scores_high(self):
        d = tempfile.mkdtemp()
        _write(d, "index.html",
               '<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1">'
               '<img srcset="a.jpg 1x" src="a.jpg"><div class="wrap">hi</div>')
        _write(d, "style.css",
               ".wrap{display:flex;max-width:100%;width:90%;padding:1rem}"
               "@media (max-width:640px){.wrap{flex-direction:column}}"
               "img{max-width:100%;height:auto}")
        r = I.responsive_audit(d)
        self.assertTrue(r["applicable"])
        self.assertGreaterEqual(r["score"], 80, r)
        self.assertEqual(r["findings"], [], r)
        self.assertEqual(r["verdict"], "mobile-ready")

    def test_nonresponsive_site_flagged(self):
        d = tempfile.mkdtemp()
        _write(d, "index.html", "<html><body><img src='a.jpg'><div class='wrap'>hi</div></body></html>")
        _write(d, "style.css", ".wrap{width:1200px}")  # fixed px, no viewport, no media
        r = I.responsive_audit(d)
        self.assertTrue(r["applicable"])
        self.assertLess(r["score"], 80, r)
        self.assertTrue(r["findings"])
        joined = " ".join(r["findings"]).lower()
        self.assertIn("viewport", joined)
        self.assertIn("media", joined)

    def test_backend_only_not_applicable(self):
        d = tempfile.mkdtemp()
        _write(d, "server.py", "print('api')")
        r = I.responsive_audit(d)
        self.assertFalse(r["applicable"])


class TestAuthGuards(unittest.TestCase):
    """git_publish / vercel_deploy must fail safe (clear needs_auth), never crash."""

    def test_git_publish_guard(self):
        # Force gh to look logged-out (empty GH_CONFIG_DIR) so we test the guard
        # deterministically and NEVER create a real repo on the user's account.
        saved = {k: os.environ.get(k) for k in ("GH_CONFIG_DIR", "GH_TOKEN", "GITHUB_TOKEN")}
        os.environ["GH_CONFIG_DIR"] = tempfile.mkdtemp()
        os.environ.pop("GH_TOKEN", None); os.environ.pop("GITHUB_TOKEN", None)
        try:
            r = I.git_publish(tempfile.mkdtemp(), "demo-repo")
            self.assertIn("ok", r)
            self.assertFalse(r["ok"])
            self.assertTrue(r.get("needs") or r.get("needs_auth"))
            self.assertIn("fix", r)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_git_publish_rejects_bad_name_when_authed(self):
        st = I.gh_status()
        if not st.get("ok"):
            self.skipTest("gh not authenticated — name validation runs after the auth gate")
        r = I.git_publish(tempfile.mkdtemp(), "bad name!!")
        self.assertFalse(r["ok"])
        self.assertIn("invalid", r["message"].lower())

    def test_vercel_guard(self):
        # No token + not logged in → needs_auth (or ok if a CLI session exists).
        os.environ.pop("VERCEL_TOKEN", None)
        r = I.vercel_status()
        self.assertIn("ok", r)
        if not r["ok"]:
            self.assertTrue(r.get("needs") or r.get("needs_auth"))
            self.assertIn("fix", r)


class TestGitState(unittest.TestCase):
    """git helpers must understand worktrees (a .git FILE, not a directory)."""

    def _repo(self, path):
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
        subprocess.run(["git", "-C", path, "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", path, "config", "user.name", "t"], check=True)
        _write(path, "f.txt", "x")
        subprocess.run(["git", "-C", path, "add", "-A"], check=True)
        subprocess.run(["git", "-C", path, "commit", "-qm", "init"], check=True)

    @unittest.skipUnless(HAS_GIT, "git not available")
    def test_detects_normal_repo_and_worktree(self):
        r = tempfile.mkdtemp()
        self._repo(r)
        gs = I.git_state(r)
        self.assertTrue(gs["is_repo"])
        self.assertFalse(gs["is_worktree"])
        wt = os.path.join(tempfile.mkdtemp(), "wt")
        subprocess.run(["git", "-C", r, "worktree", "add", "-b", "wt", wt], check=True)
        gsw = I.git_state(wt)
        self.assertTrue(gsw["is_repo"], "a worktree has a .git FILE — dir checks miss it")
        self.assertTrue(gsw["is_worktree"])

    @unittest.skipUnless(HAS_GIT, "git not available")
    def test_origin_url(self):
        r = tempfile.mkdtemp()
        self._repo(r)
        self.assertIsNone(I.origin_url(r))
        subprocess.run(["git", "-C", r, "remote", "add", "origin",
                        "https://github.com/u/r.git"], check=True)
        self.assertEqual(I.origin_url(r), "https://github.com/u/r.git")


class TestGitPublishRemoteIsolation(unittest.TestCase):
    """Publishing one app must never steal another app's remote (v0.16)."""

    def setUp(self):
        self._saved_gh = I.gh_status
        self._saved_run = I._run

    def tearDown(self):
        I.gh_status = self._saved_gh
        I._run = self._saved_run

    @unittest.skipUnless(HAS_GIT, "git not available")
    def test_existing_origin_is_reused_not_recreated(self):
        r = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=r, check=True)
        subprocess.run(["git", "-C", r, "remote", "add", "origin",
                        "https://github.com/existing/app.git"], check=True)
        created = []

        def fake_run(cmd, cwd=None, **kw):
            if cmd[:3] == ["gh", "repo", "create"]:
                created.append(cmd)  # must NEVER happen when origin exists
                return 0, "https://github.com/u/brand-new-name", ""
            if cmd[:2] == ["git", "rev-parse"]:
                key = cmd[2]
                if key == "--is-inside-work-tree":
                    return 0, "true", ""
                if key == "--show-toplevel":
                    return 0, cwd, ""
                if key == "--git-dir":
                    return 0, os.path.join(cwd, ".git"), ""
                return 0, "", ""
            if cmd[:2] == ["git", "config"] and cmd[2] == "user.email":
                return 0, "t@t", ""
            if cmd[:4] == ["git", "diff", "--cached", "--quiet"]:
                return 0, "", ""  # nothing staged → no commit needed
            if cmd[:4] == ["git", "remote", "get-url", "origin"]:
                return 0, "https://github.com/existing/app.git", ""
            if cmd[:2] == ["git", "push"]:
                return 0, "pushed", ""
            return 0, "", ""

        I.gh_status = lambda: {"ok": True, "message": "stub"}
        I._run = fake_run
        out = I.git_publish(r, "brand-new-name")
        self.assertTrue(out["ok"], out)
        self.assertTrue(out["reused_remote"])
        self.assertEqual(out["url"], "https://github.com/existing/app.git")
        self.assertEqual(created, [], "gh repo create must not run when origin exists")


if __name__ == "__main__":
    unittest.main()
