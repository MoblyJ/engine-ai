"""Deploy readiness — inventory vs. evidence separation, script parsing, real test detection."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp"))
import deploy  # noqa: E402


def _write(root, rel, text):
    full = os.path.join(root, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    open(full, "w").write(text)


class TestEvidenceParsing(unittest.TestCase):
    def test_package_json_without_start_script_is_not_startable(self):
        d = tempfile.mkdtemp()
        _write(d, "package.json", '{"name": "x", "scripts": {"dev": "vite"}}')
        rd = deploy.deploy_readiness(d)
        self.assertFalse(rd["checks"]["has_start_script"],
                         "file existence must not pass for a missing scripts.start")
        self.assertTrue(any("start" in r for r in rd["recommendations"]))

    def test_test_like_filenames_are_not_tests(self):
        d = tempfile.mkdtemp()
        _write(d, "latest.txt", "not a test")
        _write(d, "attestation.md", "sounds like testing but isn't")
        self.assertFalse(deploy._has_tests(d, deploy.detect_stack(d), {}))

    def test_real_test_files_detected_per_stack(self):
        cases = [("test_auth.py", "python"), ("auth.test.js", "node"),
                 ("auth.spec.tsx", "node"), ("auth_test.go", "go")]
        for fname, stack in cases:
            d = tempfile.mkdtemp()
            _write(d, fname, "x")
            _write(d, {"python": "requirements.txt", "node": "package.json",
                       "go": "go.mod"}[stack], "")
            self.assertTrue(deploy._has_tests(d, stack, deploy.node_scripts(d)), fname)

    def test_declared_npm_test_script_counts(self):
        d = tempfile.mkdtemp()
        _write(d, "package.json", '{"scripts": {"test": "jest"}}')
        self.assertTrue(deploy._has_tests(d, "node", deploy.node_scripts(d)))
        self.assertEqual(deploy.detect_test_framework(d, "node", deploy.node_scripts(d)), "jest")

    def test_evidence_carries_scripts_and_workflows(self):
        d = tempfile.mkdtemp()
        _write(d, "package.json", '{"scripts": {"start": "node s.js", "test": "vitest"}}')
        _write(d, ".github/workflows/ci.yml", "on: push\n")
        ev = deploy.gather_evidence(d)
        self.assertEqual(ev["scripts"]["start"], "node s.js")
        self.assertEqual(ev["ci_workflows"], ["ci.yml"])
        self.assertIsNone(ev["commit"], "non-git dir has no commit — no fake SHAs")

    def test_verify_passing_python_project(self):
        d = tempfile.mkdtemp()
        _write(d, "requirements.txt", "")
        _write(d, "test_ok.py", "import unittest\nclass T(unittest.TestCase):\n"
                               "    def test_ok(self):\n        self.assertTrue(True)\n")
        out = deploy.verify(d, timeout=60)
        self.assertTrue(out["passed"], out)
        self.assertEqual(out["checks_run"][0]["name"], "unittest")
        self.assertEqual(out["checks_run"][0]["exit_code"], 0)

    def test_verify_no_commands_is_not_passed(self):
        d = tempfile.mkdtemp()
        out = deploy.verify(d)
        self.assertFalse(out["passed"], "no runnable checks must never read as passing")


class TestScaffoldSafety(unittest.TestCase):
    def test_unknown_kind(self):
        r = deploy.scaffold(tempfile.mkdtemp(), "rust-api")
        self.assertFalse(r["ok"])

    def test_refuses_conflicts_by_default(self):
        d = tempfile.mkdtemp()
        original = '{"scripts": {"start": "node mine.js"}}'
        _write(d, "package.json", original)
        r = deploy.scaffold(d, "node-api")
        self.assertFalse(r["ok"])
        self.assertEqual(r["conflicts"], ["package.json"])
        self.assertEqual(open(os.path.join(d, "package.json")).read(), original)

    def test_dry_run_writes_nothing(self):
        d = tempfile.mkdtemp()
        r = deploy.scaffold(d, "static", dry_run=True)
        self.assertTrue(r["dry_run"])
        self.assertIn("index.html", r["would_write"])
        self.assertEqual(os.listdir(d), [])

    def test_replace_preserves_original_as_bak(self):
        d = tempfile.mkdtemp()
        _write(d, ".gitignore", "precious\n")
        r = deploy.scaffold(d, "static", replace=True)
        self.assertTrue(r["ok"])
        self.assertIn(".gitignore.engine-ai.bak", r["backups"])
        self.assertEqual(open(os.path.join(d, ".gitignore.engine-ai.bak")).read(), "precious\n")


if __name__ == "__main__":
    unittest.main()
