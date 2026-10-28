"""README must not drift: tool/expert/test counts are verified against the source."""
import os
import re
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "mcp"))


def _mcp_tool_count() -> int:
    import forge_mcp  # registers all tools at import
    return len(forge_mcp.TOOLS)


def _expert_count() -> int:
    return len([f for f in os.listdir(os.path.join(REPO, "agents"))
                if f.startswith("domain-") and f.endswith(".md")])


def _test_count() -> int:
    p = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                       cwd=REPO, capture_output=True, text=True, timeout=600)
    m = re.search(r"Ran (\d+) tests?", p.stdout + p.stderr)
    return int(m.group(1)) if m else -1


def _readme() -> str:
    with open(os.path.join(REPO, "README.md"), encoding="utf-8") as f:
        return f.read()


class TestReadmeCounts(unittest.TestCase):
    def test_tool_count_matches_source(self):
        n = _mcp_tool_count()
        stated = {int(x) for x in re.findall(r"MCP tools — (\d+)", _readme())}
        self.assertTrue(stated, "README must state the MCP tool count as 'MCP tools — N'")
        self.assertEqual(stated, {n}, f"README says {stated}, source has {n} tools")

    def test_expert_count_matches_agents_dir(self):
        n = _expert_count()
        stated = {int(x) for x in re.findall(r"(\d+) domain", _readme())}
        self.assertIn(n, stated, f"README domain-expert counts {stated} vs {n} agent files")

    def test_npx_invocations_use_scoped_package(self):
        # the bare `engine-ai` npm name is squatted by an unrelated package
        for m in re.finditer(r"npx ([^\s`]+)", _readme()):
            self.assertTrue(m.group(1).startswith("@okoboji/engine-ai"),
                            f"unscoped npx invocation: npx {m.group(1)}")

    def test_no_stale_readiness_100_claims(self):
        self.assertNotIn("readiness 100", _readme().replace("readiness-100", "readiness 100").lower()
                         .replace("deploy-readiness 100", ""), "readiness is inventory, not proof")


if __name__ == "__main__":
    unittest.main()
