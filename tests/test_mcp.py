"""Drive the MCP server over real stdio JSON-RPC, exactly as Claude Code would."""
import json
import os
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(REPO, "mcp", "forge_mcp.py")


class MCPClient:
    def __init__(self, home):
        env = {**os.environ, "ENGINE_AI_HOME": home,
               "ENGINE_AI_MASTER_KEY": "test-key"}
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
        self.assertEqual(r["result"]["serverInfo"]["name"], "engine-ai")
        self.assertIn("tools", r["result"]["capabilities"])

    def test_initialize_negotiates_unsupported_version(self):
        r = self.cli.call("initialize", {"protocolVersion": "1999-01-01"})
        self.assertIn(r["result"]["protocolVersion"], ("2024-11-05", "2025-03-26", "2025-06-18"))
        self.assertNotEqual(r["result"]["protocolVersion"], "1999-01-01")

    def test_tools_list(self):
        self.cli.call("initialize", {"protocolVersion": "2024-11-05"})
        self.cli.call("notifications/initialized", notify=True)
        r = self.cli.call("tools/list")
        names = {t["name"] for t in r["result"]["tools"]}
        for expected in ("scaffold_app", "deploy_readiness", "deploy_verify", "index_repo", "search_repo",
                         "list_skills", "set_secret", "list_secrets", "import_repo_skills", "get_skill",
                         "git_publish", "vercel_deploy", "responsive_audit",
                         "memory_save", "memory_recall", "memory_context", "memory_list", "memory_forget",
                         "memory_get", "app_create", "app_update", "app_list", "app_resume", "app_find",
                         "knowledge_ingest", "knowledge_search", "knowledge_domains", "context_pack",
                         "suggest_experts"):
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

    # ------------------------------------------------ protocol failure paths (v0.16)
    def test_malformed_json_gets_parse_error(self):
        self.cli.p.stdin.write("{not json!!\n"); self.cli.p.stdin.flush()
        r = json.loads(self.cli.p.stdout.readline())
        self.assertEqual(r["error"]["code"], -32700)

    def test_non_object_request_rejected(self):
        self.cli.p.stdin.write("[1,2,3]\n"); self.cli.p.stdin.flush()
        r = json.loads(self.cli.p.stdout.readline())
        self.assertEqual(r["error"]["code"], -32600)

    def test_missing_required_argument_is_invalid_params(self):
        r = self.cli.call("tools/call", {"name": "search_repo", "arguments": {"path": "/tmp"}})
        self.assertEqual(r["error"]["code"], -32602)
        self.assertIn("query", r["error"]["message"])

    def test_wrong_argument_type_is_invalid_params(self):
        r = self.cli.call("tools/call", {"name": "memory_forget", "arguments": {"id": "seven"}})
        self.assertEqual(r["error"]["code"], -32602)

    def test_missing_tool_name_is_invalid_params(self):
        r = self.cli.call("tools/call", {"arguments": {}})
        self.assertEqual(r["error"]["code"], -32602)

    def test_operational_failure_is_result_not_error(self):
        """ok:false from a tool is a normal payload — NOT a JSON-RPC protocol error."""
        out, err = self.cli.tool("memory_forget", {"id": 999999})
        self.assertFalse(err)  # not isError
        self.assertIn("deleted", json.dumps(out))  # operational result serialized normally

    # ------------------------------------------------ scaffold safety (v0.16)
    def test_scaffold_refuses_overwrite_and_previews(self):
        target = tempfile.mkdtemp()
        with open(os.path.join(target, "package.json"), "w") as f:
            f.write('{"scripts": {"start": "node real-app.js"}}')
        conflict, err = self.cli.tool("scaffold_app", {"path": target, "kind": "node-api"})
        self.assertFalse(conflict["ok"])
        self.assertIn("package.json", conflict["conflicts"])
        self.assertIn("replace", conflict["hint"])
        # the existing file must be untouched
        self.assertIn("real-app.js", open(os.path.join(target, "package.json")).read())
        preview, _ = self.cli.tool("scaffold_app", {"path": target, "kind": "node-api", "dry_run": True})
        self.assertTrue(preview["dry_run"])
        self.assertIn("Dockerfile", preview["would_write"])
        self.assertIn("package.json", preview["conflicts"])
        self.assertFalse(os.path.exists(os.path.join(target, "Dockerfile")), "dry_run wrote nothing")

    def test_scaffold_replace_backs_up_originals(self):
        target = tempfile.mkdtemp()
        os.makedirs(os.path.join(target, ".github", "workflows"))
        with open(os.path.join(target, ".github/workflows/ci.yml"), "w") as f:
            f.write("# PRECIOUS user CI\n")
        out, err = self.cli.tool("scaffold_app", {"path": target, "kind": "static", "replace": True})
        self.assertTrue(out["ok"])
        self.assertTrue(out["replaced"])
        bak = os.path.join(target, ".github/workflows/ci.yml.engine-ai.bak")
        self.assertEqual(open(bak).read(), "# PRECIOUS user CI\n")
        self.assertNotEqual(open(os.path.join(target, ".github/workflows/ci.yml")).read(),
                            "# PRECIOUS user CI\n")

    def test_readiness_reports_evidence_and_commit(self):
        repo = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
        subprocess.run(["git", "-C", repo, "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", repo, "config", "user.name", "t"], check=True)
        self.cli.tool("scaffold_app", {"path": repo, "kind": "python-api"})
        subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
        subprocess.run(["git", "-C", repo, "commit", "-qm", "x"], check=True)
        rd, _ = self.cli.tool("deploy_readiness", {"path": repo})
        ev = rd["evidence"]
        self.assertEqual(ev["test_framework"], "unittest")
        self.assertTrue(ev["commit"], "evidence must bind to the exact commit")
        self.assertIn("ci.yml", ev["ci_workflows"])

    def test_deploy_verify_runs_tests_and_binds_commit(self):
        repo = tempfile.mkdtemp()
        self.cli.tool("scaffold_app", {"path": repo, "kind": "python-api"})
        out, err = self.cli.tool("deploy_verify", {"path": repo, "timeout": 60})
        self.assertFalse(err, out)
        self.assertTrue(out["passed"], out)
        self.assertEqual(out["checks_run"][0]["exit_code"], 0)
        self.assertTrue(out["checks_run"][0]["duration_s"] >= 0)

    def test_deploy_verify_failing_tests_reported(self):
        repo = tempfile.mkdtemp()
        with open(os.path.join(repo, "requirements.txt"), "w") as f:
            f.write("")
        with open(os.path.join(repo, "test_broken.py"), "w") as f:
            f.write("import unittest\nclass T(unittest.TestCase):\n"
                    "    def test_fails(self):\n        self.fail('intentional')\n")
        out, _ = self.cli.tool("deploy_verify", {"path": repo, "timeout": 60})
        self.assertFalse(out["passed"])
        self.assertNotEqual(out["checks_run"][0]["exit_code"], 0)

    def test_memory_project_scoping_over_mcp(self):
        self.cli.tool("memory_save", {"keywords": ["proj-x", "api"], "context": "secret fact X",
                                      "project": "proj-x", "kind": "fact"})
        other, _ = self.cli.tool("memory_recall", {"keywords": ["proj-x", "api"], "project": "proj-y"})
        self.assertEqual(other["pockets"], [])
        same, _ = self.cli.tool("memory_recall", {"keywords": ["proj-x", "api"], "project": "proj-x"})
        self.assertTrue(same["pockets"])
        full, _ = self.cli.tool("memory_get", {"id": same["pockets"][0]["id"]})
        self.assertEqual(full["text"], "secret fact X")


if __name__ == "__main__":
    unittest.main()
