"""Tests for the GitHub / Vercel / mobile-responsive integrations."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp"))
import integrations as I  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
