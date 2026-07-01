"""Drive the MCP server over real stdio JSON-RPC, exactly as Claude Code would."""
import json
import os
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(REPO, "mcp", "forge_mcp.py")


class MCPClient:
    def __init__(self, home):
        env = {**os.environ, "MOBLY_AI_HOME": home,
               "MOBLY_AI_MASTER_KEY": "test-key"}
        self.p = subprocess.Popen(["python3", SERVER], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True, env=env, bufsize=1)
        self._id = 0

    def call(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method}
        if not notify:
            self._id += 1
            msg["id"] = self._id
        if params is not None:
            msg["params"] = params
        self.p.stdin.write(json.dumps(msg) + "\n"); self.p.stdin.flush()
        if notify:
            return None
        return json.loads(self.p.stdout.readline())

    def tool(self, name, args):
        r = self.call("tools/call", {"name": name, "arguments": args})
        text = r["result"]["content"][0]["text"]
        try:
            return json.loads(text), r["result"].get("isError", False)
        except json.JSONDecodeError:
            return text, r["result"].get("isError", False)

    def close(self):
        self.p.stdin.close(); self.p.terminate(); self.p.wait(timeout=5)


class TestMCP(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.cli = MCPClient(self.home)

    def tearDown(self):
        self.cli.close()

    def test_initialize(self):
        r = self.cli.call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}})
        self.assertEqual(r["result"]["serverInfo"]["name"], "mobly-ai")
        self.assertIn("tools", r["result"]["capabilities"])

    def test_tools_list(self):
        self.cli.call("initialize", {"protocolVersion": "2024-11-05"})
        self.cli.call("notifications/initialized", notify=True)
        r = self.cli.call("tools/list")
        names = {t["name"] for t in r["result"]["tools"]}
        for expected in ("scaffold_app", "deploy_readiness", "index_repo", "search_repo",
                         "list_skills", "set_secret", "list_secrets", "import_repo_skills", "get_skill",
                         "git_publish", "vercel_deploy", "responsive_audit"):
            self.assertIn(expected, names)

    def test_scaffold_then_readiness(self):
        target = tempfile.mkdtemp()
        out, err = self.cli.tool("scaffold_app", {"path": target, "kind": "node-api"})
        self.assertFalse(err)
        self.assertEqual(out["kind"], "node-api")
        self.assertTrue(os.path.exists(os.path.join(target, "Dockerfile")))
        self.assertTrue(os.path.exists(os.path.join(target, "server.js")))
        rd, _ = self.cli.tool("deploy_readiness", {"path": target})
        self.assertEqual(rd["stack"], "node")
        self.assertTrue(rd["checks"]["has_dockerfile"])
        self.assertTrue(rd["checks"]["has_ci"])
        self.assertGreaterEqual(rd["score"], 80)

    def test_scaffold_python_and_static(self):
        for kind, marker in (("python-api", "app.py"), ("static", "index.html")):
            t = tempfile.mkdtemp()
            out, err = self.cli.tool("scaffold_app", {"path": t, "kind": kind})
            self.assertFalse(err, out)
            self.assertTrue(os.path.exists(os.path.join(t, marker)))

    def test_index_and_search(self):
        repo = tempfile.mkdtemp()
        with open(os.path.join(repo, "auth.py"), "w") as f:
            f.write("def login(user):\n    # authenticate against the session store\n    return True\n")
        with open(os.path.join(repo, "util.py"), "w") as f:
            f.write("def add(a,b):\n    # arithmetic helper\n    return a+b\n")
        idx, _ = self.cli.tool("index_repo", {"path": repo})
        self.assertEqual(idx["files"], 2)
        hits, _ = self.cli.tool("search_repo", {"path": repo, "query": "authenticate login session"})
        self.assertTrue(hits)
        self.assertIn("auth.py", hits[0]["path"])

    def test_secrets(self):
        _, err = self.cli.tool("set_secret", {"name": "API_KEY", "value": "topsecret"})
        self.assertFalse(err)
        out, _ = self.cli.tool("list_secrets", {})
        self.assertIn("API_KEY", out["names"])
        self.assertNotIn("topsecret", json.dumps(out))

    def test_import_real_repo_skills(self):
        base = os.path.dirname(REPO)
        ag = os.path.join(base, "agent-skills")
        if not os.path.isdir(ag):
            self.skipTest("agent-skills not present")
        out, _ = self.cli.tool("import_repo_skills", {"path": ag})
        self.assertGreaterEqual(out["imported"], 20)
        skills, _ = self.cli.tool("list_skills", {"query": "test"})
        self.assertTrue(any("test" in s["name"] for s in skills))

    def test_unknown_tool_errors(self):
        r = self.cli.call("tools/call", {"name": "nope", "arguments": {}})
        self.assertIn("error", r)


if __name__ == "__main__":
    unittest.main()
