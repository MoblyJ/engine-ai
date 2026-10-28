"""Build tools — workers can actually read/write/run inside the workspace."""
import json
import os
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from engine_core.tools.executor import Executor  # noqa: E402
from engine_core.tools.policy import Policy  # noqa: E402
from engine_core.tools.registry import build_registry  # noqa: E402


def _exec(root):
    return Executor(build_registry(), Policy(root=root),
                    journal_path=None)


class TestBuildTools(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()
        self.ex = _exec(self.ws)

    def _call(self, name, args):
        return self.ex.execute({"id": "c1", "function": {"name": name,
                                                         "arguments": json.dumps(args)}},
                               context={"workspace": self.ws, "task_id": "t"})

    def test_write_read_list_roundtrip(self):
        w = self._call("write_file", {"path": "src/hello.py", "content": "print('hi')\n"})
        self.assertTrue(w["ok"], w)
        r = self._call("read_file", {"path": "src/hello.py"})
        self.assertIn("print('hi')", r["content"])
        d = self._call("list_dir", {"path": "src"})
        self.assertIn("hello.py", str(d["content"]))

    def test_run_command_in_workspace(self):
        self._call("write_file", {"path": "hi.sh", "content": ""})
        out = self._call("run_command", {"command": ["bash", "-c", "echo built > out.txt"]})
        self.assertTrue(out["ok"], out)
        self.assertTrue(os.path.exists(os.path.join(self.ws, "out.txt")))

    def test_run_command_string_form(self):
        out = self._call("run_command", {"command": "echo hello"})
        self.assertIn("hello", out["content"])

    def test_policy_blocks_writes_outside_the_workspace(self):
        outside = os.path.join(tempfile.mkdtemp(), "evil.txt")
        out = self._call("write_file", {"path": outside})
        self.assertFalse(out["ok"])
        self.assertTrue(out["blocked"])

    def test_read_only_policy_blocks_write_tools(self):
        ex = Executor(build_registry(), Policy(read_only=True, root=self.ws))
        out = ex.execute({"id": "c", "function": {"name": "write_file",
                          "arguments": '{"path": "x", "content": "y"}'}},
                         context={"workspace": self.ws})
        self.assertFalse(out["ok"])
        self.assertTrue(out["blocked"])

    def test_command_failure_is_reported_not_raised(self):
        out = self._call("run_command", {"command": ["bash", "-c", "exit 3"]})
        self.assertFalse(out["ok"])
        self.assertIn("3", out["content"])


if __name__ == "__main__":
    unittest.main()
