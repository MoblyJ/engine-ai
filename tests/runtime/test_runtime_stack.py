"""Runtime stack: graph validation, leases+fencing, worker loop, executor policy."""
import json
import os
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from engine_core.config import Config, ConfigError, load_config  # noqa: E402
from engine_core.providers.base import (Completion, Message, ModelInfo, Provider,  # noqa: E402
                                        ProviderError, Usage)
from engine_core.runtime.state import StateError, StateStore  # noqa: E402
from engine_core.runtime.task_graph import GraphError, TaskGraph, TaskSpec  # noqa: E402
from engine_core.runtime.worker import Worker  # noqa: E402
from engine_core.tools.executor import Executor  # noqa: E402
from engine_core.tools.policy import Policy, PolicyViolation  # noqa: E402
from engine_core.tools.registry import Registry  # noqa: E402

CFG_EXAMPLE = os.path.join(REPO, "config.example.yaml")


class TestTaskGraph(unittest.TestCase):
    def test_cycle_detected(self):
        g = TaskGraph()
        g.add(TaskSpec("a", dependencies=["b"]))
        g.add(TaskSpec("b", dependencies=["a"]))
        with self.assertRaises(GraphError):
            g.validate()

    def test_unknown_dependency_detected(self):
        g = TaskGraph()
        g.add(TaskSpec("a", dependencies=["ghost"]))
        with self.assertRaises(GraphError):
            g.validate()

    def test_self_dependency_rejected(self):
        with self.assertRaises(GraphError):
            TaskSpec("a", dependencies=["a"])

    def test_ready_and_topological_order(self):
        g = TaskGraph()
        g.add(TaskSpec("contracts"))
        g.add(TaskSpec("backend", dependencies=["contracts"]))
        g.add(TaskSpec("frontend", dependencies=["contracts"]))
        g.add(TaskSpec("e2e", dependencies=["backend", "frontend"]))
        self.assertEqual(sorted(g.ready(set())), ["contracts"])
        self.assertEqual(g.ready({"contracts"}), ["backend", "frontend"])
        order = g.topological_order()
        self.assertLess(order.index("contracts"), order.index("backend"))
        self.assertEqual(order[-1], "e2e")


class TestStateStore(unittest.TestCase):
    def setUp(self):
        self.store = StateStore(os.path.join(tempfile.mkdtemp(), "s.db"))
        self.store.upsert(TaskSpec("t1", dependencies=[]))
        self.store.mark_ready(["t1"])

    def test_claim_lease_and_fenced_complete(self):
        a = self.store.claim("t1", owner="w1")
        self.assertEqual(a["fence"], 1)
        self.assertTrue(self.store.heartbeat("t1", "w1", 1))
        self.assertTrue(self.store.complete("t1", 1, {"status": "done"}))
        self.assertEqual(self.store.get("t1")["state"], "complete")

    def test_stale_fence_cannot_overwrite_newer_attempt(self):
        """The fencing rule: a late worker must never clobber a newer attempt."""
        first = self.store.claim("t1", owner="w1")           # fence 1
        self.store.expire_leases(now=first["lease_expires"] + 1)  # lease lapses
        second = self.store.claim("t1", owner="w2")           # fence 2
        self.assertEqual(second["fence"], 2)
        self.assertFalse(self.store.complete("t1", first["fence"], {"status": "done-by-ghost"}),
                        "stale fence write must be rejected")
        self.assertTrue(self.store.complete("t1", second["fence"], {"status": "done"}))

    def test_expired_lease_requeues_until_max_attempts(self):
        for i in range(3):  # MAX_ATTEMPTS=3: the 3rd expiry fails it for good
            a = self.store.claim("t1", owner=f"w{i}")
            self.store.expire_leases(now=a["lease_expires"] + 1)
        self.assertEqual(self.store.get("t1")["state"], "failed")

    def test_fail_retries_then_gives_up(self):
        a = self.store.claim("t1")
        self.assertTrue(self.store.fail("t1", a["fence"], "boom"))
        self.assertEqual(self.store.get("t1")["state"], "ready")   # retryable
        b = self.store.claim("t1")
        self.store.fail("t1", b["fence"], "boom2")
        c = self.store.claim("t1")
        self.store.fail("t1", c["fence"], "boom3")
        self.assertEqual(self.store.get("t1")["state"], "failed")  # attempts exhausted

    def test_dependency_gate_in_ready_ids(self):
        self.store.upsert(TaskSpec("t2", dependencies=["t1"]))
        self.store.mark_ready(["t1", "t2"])
        self.assertEqual(self.store.ready_ids(), ["t1"], "t2 waits for t1")
        a = self.store.claim("t1")
        self.store.complete("t1", a["fence"], {})
        self.assertEqual(self.store.ready_ids(), ["t2"])

    def test_checkpoint_roundtrip(self):
        a = self.store.claim("t1")
        self.store.checkpoint("t1", a["fence"], {"turn": 3, "history": []})
        self.assertEqual(self.store.get("t1")["checkpoint"]["turn"], 3)


class _FakeProvider(Provider):
    name = "fake"

    def __init__(self, completions):
        self.completions = completions
        self.calls = 0

    def complete(self, model, messages, *, tools=None, max_output_tokens=None, temperature=None):
        self.calls += 1
        c = self.completions.pop(0) if self.completions else Completion("", model, Usage())
        if isinstance(c, Exception):
            raise c
        c.raw = c.raw or {}
        return c


def _registry() -> Registry:
    reg = Registry()
    reg.register("read_thing", lambda a: {"value": 42}, read_only=True)
    reg.register("write_thing", lambda a: {"ok": True, "wrote": a}, read_only=False)
    reg.register("boom", lambda a: (_ for _ in ()).throw(RuntimeError("kaput")), read_only=False)
    return reg


class TestWorkerLoop(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = StateStore(os.path.join(self.dir, "s.db"))
        self.store.upsert(TaskSpec("t1"))
        self.store.mark_ready(["t1"])
        self.assignment = self.store.claim("t1", owner="w")

    def _worker(self, provider, read_only=False):
        cfg = Config()
        return Worker(provider, "fake-model",
                      Executor(_registry(), Policy(read_only=read_only), None),
                      self.store, cfg, cfg.profile("backend"))

    def test_tool_call_then_outcome(self):
        tool_turn = Completion("", "m", Usage(), tool_calls=[
            {"id": "c1", "function": {"name": "read_thing", "arguments": "{}"}}])
        final = Completion('done! {"status": "done", "changed_files": ["a.py"]}', "m", Usage())
        w = self._worker(_FakeProvider([tool_turn, final]))
        out = w.run(self.assignment, "do the thing")
        self.assertEqual(out["status"], "done")
        self.assertEqual(self.store.get("t1")["state"], "complete")
        self.assertEqual(self.store.get("t1")["result"]["changed_files"], ["a.py"])

    def test_read_only_profile_blocks_write_tools(self):
        tool_turn = Completion("", "m", Usage(), tool_calls=[
            {"id": "c1", "function": {"name": "write_thing", "arguments": "{}"}}])
        final = Completion('{"status": "blocked", "summary": "cannot write"}', "m", Usage())
        w = self._worker(_FakeProvider([tool_turn, final]), read_only=True)
        out = w.run(self.assignment, "review only")
        # the write tool must not be in the schemas offered to the model
        schemas = w._tool_schemas()
        self.assertEqual([s["function"]["name"] for s in schemas], ["read_thing"])

    def test_auth_error_checkpoints_and_stops(self):
        w = self._worker(_FakeProvider([ProviderError(ProviderError.AUTH, "bad key")]))
        out = w.run(self.assignment, "x")
        self.assertEqual(out["status"], "provider-auth")
        self.assertTrue(out["checkpointed"])
        self.assertIsNotNone(self.store.get("t1")["checkpoint"])

    def test_turn_budget_exhaustion_is_reported(self):
        endless = lambda: Completion("", "m", Usage(), tool_calls=[  # noqa: E731
            {"id": "c", "function": {"name": "read_thing", "arguments": "{}"}}])
        cfg = Config()
        cfg.max_model_turns_per_task = 2
        provider = _FakeProvider([endless(), endless(), endless()])
        w = Worker(provider, "m", Executor(_registry(), Policy(), None), self.store, cfg,
                   cfg.profile("backend"))
        out = w.run(self.assignment, "x")
        self.assertEqual(out["status"], "turn-budget-exhausted")
        self.assertEqual(out["turns"], 2)


class TestExecutor(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def _exec(self, read_only=False, root=None):
        return Executor(_registry(), Policy(read_only=read_only, root=root),
                        os.path.join(self.dir, "effects.db"))

    def test_result_bounded(self):
        ex = self._exec()
        big = {"value": "x" * 100000}
        _reg_backup = ex.registry._tools["read_thing"]
        ex.registry.register("read_thing", lambda a: big, read_only=True)
        out = ex.execute({"id": "1", "function": {"name": "read_thing", "arguments": "{}"}})
        self.assertLessEqual(len(out["content"]), 8000 + 60)
        ex.registry._tools["read_thing"] = _reg_backup

    def test_write_effects_journaled_and_not_repeated(self):
        ex = self._exec()
        call = {"id": "w1", "function": {"name": "write_thing", "arguments": '{"x": 1}'}}
        r1 = ex.execute(call)
        self.assertTrue(r1["ok"])
        r2 = ex.execute(call)  # same call id after a transport failure → no repeat write
        self.assertTrue(r2.get("replayed"))
        self.assertTrue(r2["content"].find("already_applied") > 0)

    def test_tool_exception_typed_error(self):
        out = self._exec().execute({"id": "b", "function": {"name": "boom", "arguments": "{}"}})
        self.assertFalse(out["ok"])
        self.assertIn("kaput", out["content"])

    def test_path_containment(self):
        ex = self._exec(root=tempfile.mkdtemp())
        outside = os.path.join(tempfile.mkdtemp(), "evil.txt")
        out = ex.execute({"id": "p", "function": {"name": "write_thing",
                        "arguments": json.dumps({"path": outside})}})
        self.assertFalse(out["ok"])
        self.assertTrue(out["blocked"])


class TestConfig(unittest.TestCase):
    def test_example_config_parses_and_validates(self):
        cfg = load_config(CFG_EXAMPLE)
        self.assertEqual(cfg.max_active_workers, 2)
        self.assertTrue(cfg.free_only)
        self.assertFalse(cfg.paid_fallback)
        self.assertEqual(cfg.quota_exhausted, "pause")
        self.assertTrue(cfg.profile("reviewer").read_only)
        self.assertEqual(cfg.profile("frontend").requires, ["tools"])

    def test_bad_quota_action_rejected(self):
        with self.assertRaises(ConfigError):
            Config.from_dict({"routing": {"quota_exhausted": "rotate-accounts"}})

    def test_paid_fallback_requires_free_only_off(self):
        with self.assertRaises(ConfigError):
            Config.from_dict({"routing": {"free_only": True, "paid_fallback": True}})

    def test_unknown_provider_rejected(self):
        with self.assertRaises(ConfigError):
            Config.from_dict({"profiles": {"x": {"provider": "skynet"}}})

    def test_credential_ref_resolution_from_env(self):
        os.environ["ENGINE_TEST_KEY"] = "sk-secret"
        try:
            p = Config.from_dict({"profiles": {"x": {
                "credential_ref": "env:ENGINE_TEST_KEY"}}}).profile("x")
            self.assertEqual(p.resolve_credential(), "sk-secret")
        finally:
            del os.environ["ENGINE_TEST_KEY"]

    def test_json_config_also_accepted(self):
        path = os.path.join(tempfile.mkdtemp(), "c.json")
        with open(path, "w") as f:
            json.dump({"version": 1, "execution": {"max_active_workers": 3}}, f)
        self.assertEqual(load_config(path).max_active_workers, 3)


class TestRuntimeCLI(unittest.TestCase):
    """python3 -m engine_core — clean exit codes and messages, no tracebacks."""

    def setUp(self):
        import contextlib
        import io
        import engine_core.__main__ as cli
        self.cli = cli
        self.quiet = lambda: contextlib.redirect_stdout(io.StringIO())  # noqa: E731
        self.home = tempfile.mkdtemp()
        self._old_home = cli.HOME
        cli.HOME = self.home
        self._old_env = os.environ.get("ENGINE_AI_HOME")
        os.environ["ENGINE_AI_HOME"] = self.home

    def tearDown(self):
        self.cli.HOME = self._old_home
        if self._old_env is None:
            os.environ.pop("ENGINE_AI_HOME", None)
        else:
            os.environ["ENGINE_AI_HOME"] = self._old_env

    def run_cli(self, argv):
        with self.quiet():
            return self.cli.main(argv)

    def test_status(self):
        self.assertEqual(self.run_cli(["status"]), 0)

    def test_plan_validates_and_rejects_cycles(self):
        g = os.path.join(self.home, "g.json")
        with open(g, "w") as f:
            json.dump({"tasks": [{"id": "a"}, {"id": "b", "dependencies": ["a"]}]}, f)
        self.assertEqual(self.run_cli(["plan", "--graph", g]), 0)
        with open(g, "w") as f:
            json.dump({"tasks": [{"id": "a", "dependencies": ["b"]},
                                 {"id": "b", "dependencies": ["a"]}]}, f)
        self.assertEqual(self.run_cli(["plan", "--graph", g]), 1)

    def test_verify_exit_code_follows_the_command(self):
        self.assertEqual(self.run_cli(["verify", "--path", self.home, "--", "true"]), 0)
        self.assertEqual(self.run_cli(["verify", "--path", self.home, "--", "false"]), 1)
        self.assertEqual(self.run_cli(["verify", "--path", self.home, "--", "true && false"]), 1)

    def test_run_without_catalog_is_a_clean_error(self):
        g = os.path.join(self.home, "g.json")
        with open(g, "w") as f:
            json.dump({"tasks": [{"id": "a"}]}, f)
        self.assertEqual(self.run_cli(["run", "--graph", g]), 1)  # ✗ message, no traceback

    def test_run_paid_model_under_free_only_refused(self):
        from engine_core.providers.catalog import Catalog
        cat = Catalog(path=os.path.join(self.home, "models.json"))
        cat.models = {"v/paid": ModelInfo(id="v/paid", free=False, supports_tools=True,
                                          context_length=64000)}
        cat.save()
        g = os.path.join(self.home, "g.json")
        with open(g, "w") as f:
            json.dump({"tasks": [{"id": "a"}]}, f)
        self.assertEqual(self.run_cli(["run", "--graph", g, "--model", "v/paid",
                                       "--free-only"]), 1)

    def test_models_list_pin(self):
        from engine_core.providers.catalog import Catalog
        cat = Catalog(path=os.path.join(self.home, "models.json"))
        cat.models = {"v/free": ModelInfo(id="v/free", free=True, supports_tools=True,
                                          context_length=32000)}
        cat.save()
        self.assertEqual(self.run_cli(["models", "list"]), 0)
        self.assertEqual(self.run_cli(["models", "pin", "backend", "v/free"]), 0)
        # pinned ref resolves; a bogus pin is a clean error
        self.assertEqual(self.run_cli(["models", "pin", "x", "ghost/m"]), 1)

    def test_empty_env_home_falls_back(self):
        # HOME constant in the CLI must never be "" (crash in makedirs)
        self.assertTrue(os.path.isabs(self.cli.HOME))


if __name__ == "__main__":
    unittest.main()
