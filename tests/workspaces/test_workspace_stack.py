"""Workspace stack: worktree isolation, integrator conflicts, context packets, symbols."""
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from engine_core.context.compiler import CodeSpan, ContextPacket, estimate_tokens  # noqa: E402
from engine_core.context.symbols import extract_file, lookup  # noqa: E402
from engine_core.runtime.task_graph import TaskSpec  # noqa: E402
from engine_core.verification.evidence import EvidenceManifest  # noqa: E402
from engine_core.verification.runner import Verifier  # noqa: E402
from engine_core.workspaces.git import WorkspaceError, WorkspaceManager  # noqa: E402
from engine_core.workspaces.integration import IntegrationBlocked, Integrator  # noqa: E402

HAS_GIT = subprocess.run(["bash", "-c", "command -v git"], capture_output=True).returncode == 0


def _sh(*cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def _init_repo(path) -> str:
    _sh("git", "init", "-q", "-b", "main", cwd=path)
    _sh("git", "config", "user.email", "t@t", cwd=path)
    _sh("git", "config", "user.name", "t", cwd=path)
    with open(os.path.join(path, "base.txt"), "w") as f:
        f.write("base\n")
    _sh("git", "add", "-A", cwd=path)
    _sh("git", "commit", "-qm", "init", cwd=path)
    return path


class TestWorkspaceManager(unittest.TestCase):
    @unittest.skipUnless(HAS_GIT, "git not available")
    def test_create_validate_isolate(self):
        repo = _init_repo(tempfile.mkdtemp())
        wm = WorkspaceManager(repo)
        w1 = wm.create("task-1")
        w2 = wm.create("task-2")
        self.assertEqual(w1["branch"], "engine/task-1")
        self.assertTrue(w1["base_sha"])
        self.assertNotEqual(w1["path"], w2["path"])
        with open(os.path.join(w1["path"], "a.txt"), "w") as f:
            f.write("A")
        self.assertFalse(os.path.exists(os.path.join(w2["path"], "a.txt")), "worktrees isolated")
        self.assertEqual(wm.current_sha(w1["path"]), w1["base_sha"])

    @unittest.skipUnless(HAS_GIT, "git not available")
    def test_changed_files_and_commit(self):
        repo = _init_repo(tempfile.mkdtemp())
        wm = WorkspaceManager(repo)
        w = wm.create("feat")
        with open(os.path.join(w["path"], "new.txt"), "w") as f:
            f.write("x")
        self.assertIn("new.txt", wm.changed_files(w["path"]))
        sha = wm.commit_all(w["path"], "feat: add new.txt")
        self.assertNotEqual(sha, w["base_sha"])
        self.assertEqual(wm.changed_files(w["path"]), [])

    def test_non_repo_rejected_explicitly(self):
        with self.assertRaises(WorkspaceError):
            WorkspaceManager(tempfile.mkdtemp())


class TestIntegrator(unittest.TestCase):
    @unittest.skipUnless(HAS_GIT, "git not available")
    def _setup(self):
        repo = _init_repo(tempfile.mkdtemp())
        wm = WorkspaceManager(repo)
        for name, content in (("t1", "one\n"), ("t2", "two\n")):
            w = wm.create(name)
            with open(os.path.join(w["path"], f"{name}.txt"), "w") as f:
                f.write(content)
            wm.commit_all(w["path"], f"{name}")
        return repo, wm

    @unittest.skipUnless(HAS_GIT, "git not available")
    def test_two_tasks_integrate(self):
        repo, wm = self._setup()
        with Integrator(repo) as integ:
            out = integ.integrate(["engine/t1", "engine/t2"])
        self.assertTrue(out["ok"])
        files = _sh("git", "ls-files", cwd=repo).stdout.split()
        self.assertIn("t1.txt", files)
        self.assertIn("t2.txt", files)

    @unittest.skipUnless(HAS_GIT, "git not available")
    def test_conflict_blocks_with_explicit_resolution_needed(self):
        repo, wm = self._setup()
        w = wm.create("clash")  # branch from HEAD BEFORE the mainline edit
        with open(os.path.join(repo, "base.txt"), "w") as f:
            f.write("mainline change\n")
        _sh("git", "commit", "-am", "mainline edit", cwd=repo)
        with open(os.path.join(w["path"], "base.txt"), "w") as f:
            f.write("conflicting change\n")
        wm.commit_all(w["path"], "clash")
        with Integrator(repo) as integ:
            with self.assertRaises(IntegrationBlocked) as cm:
                integ.integrate(["engine/clash"])
            self.assertTrue(any("base.txt" in c for c in cm.exception.conflicts)
                            or cm.exception.conflicts == [])
        # no ours/theirs auto-resolution: the tree is back to a clean state
        self.assertEqual(_sh("git", "status", "--porcelain", cwd=repo).stdout.strip(), "")

    @unittest.skipUnless(HAS_GIT, "git not available")
    def test_failed_verification_reverts_merge(self):
        repo, wm = self._setup()
        w = wm.create("bad")
        with open(os.path.join(w["path"], "t1.txt"), "w") as f:
            f.write("breaks it\n")
        wm.commit_all(w["path"], "bad")
        with Integrator(repo, verifier=lambda ws: {"passed": False, "checks": []}) as integ:
            out = integ.integrate(["engine/t1", "engine/bad"])
        self.assertFalse(out["ok"])
        self.assertTrue(out["revert_ok"])
        self.assertNotIn("t1.txt", _sh("git", "ls-files", cwd=repo).stdout.split(),
                         "the failing merge must not stay merged")


class TestContextPacket(unittest.TestCase):
    def test_budget_sections_and_truncation(self):
        packet = ContextPacket(task_id="t", objective="build it", budget_tokens=1000,
                               code_spans=[CodeSpan("big.py", 1, 900, "x" * 99999)])
        rendered = packet.render()
        self.assertLessEqual(len(rendered), 1000 * 4 + 600,
                             "packet must stay within its token budget (+ headers)")
        self.assertIn("…(code truncated to budget)", rendered)

    def test_build_from_task(self):
        task = TaskSpec("t9", title="add auth", objective="JWT login",
                        acceptance=["login works"], owned_paths=["src/auth.py"],
                        base_sha="abc123", shared_contract_version="contracts/v2")
        packet = ContextPacket.build(task, [], decisions=["use HS256"])
        self.assertIn("JWT login", packet.render())
        self.assertIn("contracts/v2", packet.render())
        self.assertIn("abc123", packet.render())

    def test_estimate_tokens(self):
        self.assertEqual(estimate_tokens("x" * 40), 10)


class TestSymbols(unittest.TestCase):
    def test_python_spans_precise(self):
        src = ("class A:\n"
               "    def one(self):\n"
               "        return 1\n"
               "\n"
               "def two():\n"
               "    return 2\n")
        d = tempfile.mkdtemp()
        p = os.path.join(d, "m.py")
        open(p, "w").write(src)
        spans = extract_file(p)
        by_name = {s.name: s for s in spans}
        self.assertEqual(by_name["A"].start_line, 1)
        self.assertEqual(by_name["A"].end_line, 3)
        self.assertEqual(by_name["two"].start_line, 5)
        self.assertEqual([s.name for s in lookup(spans, "A")], ["A"])

    def test_js_spans_regex(self):
        src = "export function hello() {\n  return 1;\n}\nconst x = 2;\n"
        d = tempfile.mkdtemp()
        p = os.path.join(d, "m.js")
        open(p, "w").write(src)
        spans = extract_file(p)
        self.assertIn("hello", [s.name for s in spans])


class TestVerifierAndEvidence(unittest.TestCase):
    def test_verifier_records_exit_codes(self):
        d = tempfile.mkdtemp()
        v = Verifier()
        out = v.run_checks(d, [["true"], ["false"]])
        self.assertFalse(out["passed"])
        self.assertEqual([c["exit_code"] for c in out["checks"]], [0, 1])

    def test_missing_command_is_not_passed(self):
        out = Verifier().run_checks(tempfile.mkdtemp(), [["definitely-not-a-real-cmd-xyz"]])
        self.assertFalse(out["passed"])

    def test_evidence_manifest_hashable(self):
        m = EvidenceManifest(subject="t1", commit="abc",
                             checks=[{"name": "pytest", "exit_code": 0}])
        self.assertTrue(m.passed())
        h1 = m.manifest_hash()
        m.checks.append({"name": "e2e", "exit_code": 0})
        self.assertNotEqual(m.manifest_hash(), h1, "different evidence → different hash")
        m.add_artifact("log", b"hello")
        self.assertEqual(m.artifacts["log"], hashlib.sha256(b"hello").hexdigest())


if __name__ == "__main__":
    unittest.main()
