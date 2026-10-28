"""Multi-agent build pipeline: plan → per-profile execution → verification."""
import json
import os
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from engine_core.config import Config  # noqa: E402
from engine_core.pipeline import BuildPipeline, PipelineError, extract_json  # noqa: E402
from engine_core.profiles import Assignments  # noqa: E402
from engine_core.providers.base import Completion, Message, Provider, Usage  # noqa: E402
from engine_core.runtime.task_graph import TaskSpec  # noqa: E402

PLAN = json.dumps({"tasks": [
    {"id": "contracts", "title": "API contract", "profile": "backend",
     "objective": "write the shared API contract", "acceptance": ["contract file"]},
    {"id": "impl", "title": "implement", "profile": "backend",
     "dependencies": ["contracts"], "objective": "implement the endpoints"},
    {"id": "tests", "title": "tests", "profile": "test-author",
     "dependencies": ["contracts"], "objective": "write tests",
     "verification": ["python3 -c 'exit(0)'"]},
]})


class FakeProvider(Provider):
    name = "fake"
    def __init__(self, script):
        self.script = list(script)
    def complete(self, model, messages, *, tools=None, max_output_tokens=None, temperature=None):
        item = self.script.pop(0) if self.script else Completion("{}", model, Usage())
        if isinstance(item, Exception):
            raise item
        return Completion(item, model, Usage(input_tokens=10, output_tokens=5))


DONE = '{"status": "done", "summary": "ok", "changed_files": []}'


def _pipeline(home, plan_text, worker_texts=None, verifier=None):
    """A pipeline whose planner returns plan_text and workers return worker_texts."""
    assignments = Assignments(home)
    # every profile gets a fake key so _provider_for never blocks
    for name in assignments.profiles:
        assignments.set(name, credential_ref="fake-key", model_ref="fake/model")
    made = []

    def factory(profile, api_key):
        if not made:  # first provider built is the planner's
            made.append(FakeProvider([plan_text]))
            return made[0]
        p = FakeProvider(worker_texts or [DONE])
        made.append(p)
        return p

    import engine_core.tools.registry as R
    reg = R.Registry()
    reg.register("noop", lambda a: {"ok": True}, read_only=True)
    return BuildPipeline(home, Config(), assignments, provider_factory=factory,
                         registry=reg, verifier=verifier), made


class TestExtractJson(unittest.TestCase):
    def test_prose_and_fences_tolerated(self):
        text = "Here's my plan:\n```json\n{\"tasks\": [{\"id\": \"a\"}]}\n```\nhth"
        self.assertEqual(extract_json(text)["tasks"][0]["id"], "a")

    def test_unparseable_raises_with_head(self):
        with self.assertRaises(PipelineError) as cm:
            extract_json("no json at all")
        self.assertIn("output head", str(cm.exception))


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()

    def test_plan_produces_validated_graph(self):
        pipe, _ = _pipeline(self.home, PLAN)
        plan = pipe.plan("build a small API")
        self.assertEqual(sorted(plan["graph"].tasks),
                         ["contracts", "impl", "tests"])
        self.assertEqual(plan["graph"].tasks["impl"].dependencies, ["contracts"])

    def test_plan_invalid_json_is_typed_error(self):
        pipe, _ = _pipeline(self.home, "I'll do my best, boss")
        with self.assertRaises(PipelineError):
            pipe.plan("x")

    def test_plan_cycle_rejected_before_any_worker_runs(self):
        cyc = json.dumps({"tasks": [
            {"id": "a", "dependencies": ["b"]}, {"id": "b", "dependencies": ["a"]}]})
        pipe, made = _pipeline(self.home, cyc)
        with self.assertRaises(Exception):
            pipe.plan("x")
        self.assertEqual(len(made), 1, "only the planner provider was built")

    def test_execute_runs_each_task_under_its_profile(self):
        pipe, _ = _pipeline(self.home, PLAN, worker_texts=[DONE, DONE, DONE])
        out = pipe.run("build a small API")
        self.assertEqual(out["complete"], 3, out["results"])
        statuses = {r["task"]: r["status"] for r in out["results"]}
        self.assertEqual(set(statuses.values()), {"done"})

    def test_failed_verification_marks_task(self):
        import engine_core.verification.runner as VR

        class FailingVerifier(VR.Verifier):
            def run_checks(self, workspace, commands, commit=None):
                return {"commit": commit, "workspace": workspace,
                        "checks": [{"name": "x", "command": commands[0] if commands else [],
                                    "exit_code": 1, "duration_s": 0, "output_tail": "boom"}],
                        "passed": False}
        pipe, _ = _pipeline(self.home, PLAN, worker_texts=[DONE, DONE, DONE],
                            verifier=FailingVerifier())
        out = pipe.run("build a small API")
        statuses = {r["task"]: r["status"] for r in out["results"]}
        self.assertEqual(statuses["tests"], "verification-failed")
        self.assertTrue(any(e["task"] == "tests" and not e["passed"]
                            for e in out["evidence"]))

    def test_missing_key_blocks_cleanly(self):
        pipe = BuildPipeline(self.home, Config())  # no keys at all
        with self.assertRaises(PipelineError) as cm:
            pipe.plan("x")
        self.assertIn("no API key", str(cm.exception))

    def test_objective_required(self):
        pipe, _ = _pipeline(self.home, PLAN)
        with self.assertRaises(PipelineError):
            pipe.run("   ")


if __name__ == "__main__":
    unittest.main()
