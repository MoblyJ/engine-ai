"""The multi-agent build pipeline — keys.csv → 10 agents → shipped work.

One objective in; the AGENTS do the rest, each under its own API key + model:
the planner ideates and decomposes into a task graph, workers execute their
packets (frontend tasks under the frontend key, backend under backend's…),
results and decisions flow through shared project memory + structured outcomes,
and every task is verified before it counts.

Design notes (upgrade.md §3-§5):
- Shared context = the project memory store + per-task ContextPackets — never
  transcript forwarding.
- One SHARED rate limiter (account-group bucket) across profiles by default:
  free-tier budgets are small; 2 sequential workers per wave.
- Quota/auth provider errors stop the run with a checkpoint and a clean
  message — never key rotation, never endless retries.
- The plan is a VALIDATED TaskGraph (ids, deps, cycles) before anything runs;
- unparseable plans are a typed error carrying the model's output head.
"""

from __future__ import annotations

import json
import os
import re
import time

from engine_core.config import Config
from engine_core.context.compiler import ContextPacket
from engine_core.profiles import Assignments
from engine_core.providers.base import Completion, Message, ProviderError, Usage
from engine_core.providers.limiter import Limits, RateLimiter
from engine_core.providers.openrouter import OpenRouterProvider
from engine_core.runtime.state import StateStore
from engine_core.runtime.task_graph import GraphError, TaskGraph, TaskSpec
from engine_core.runtime.worker import Worker
from engine_core.tools.executor import Executor
from engine_core.tools.policy import Policy
from engine_core.tools.registry import Registry, build_registry
from engine_core.verification.evidence import EvidenceManifest
from engine_core.verification.runner import Verifier

PLANNER_SYSTEM = (
    "You are engine-ai's planner. Decompose the objective into a MINIMAL task graph "
    "(2-6 tasks) that a team of specialist agents can execute independently. Reply with "
    "ONLY a JSON object, no prose: {\"tasks\":[{"
    "\"id\":\"kebab-case-id\", \"title\":\"...\", \"profile\":\"backend|frontend|services|"
    "database|test-author|reviewer|security-review|researcher|documentation|planner\", "
    "\"dependencies\":[\"other-task-ids\"], \"objective\":\"one bounded packet\", "
    "\"acceptance\":[\"criteria\"], \"owned_paths\":[\"paths\"], "
    "\"verification\":[\"command arrays like 'python3 -m unittest'\"]}]}. "
    "Shared contracts/interfaces come FIRST as their own task; keep the graph shallow."
)


class PipelineError(RuntimeError):
    pass


def extract_json(text: str) -> dict:
    """Pull the first balanced JSON object out of model output (tolerates prose/fences)."""
    text = re.sub(r"```(?:json)?|```", "", text)
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(text[start:i + 1])
                        if isinstance(data, dict):
                            return data
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    raise PipelineError("planner did not return a JSON object; output head: "
                        + text[:300].replace("\n", " "))


class BuildPipeline:
    def __init__(self, home: str | None = None, config: Config | None = None,
                 assignments: Assignments | None = None,
                 provider_factory=None, registry: Registry | None = None,
                 limiter: RateLimiter | None = None, verifier: Verifier | None = None):
        self.home = home or (os.environ.get("ENGINE_AI_HOME")
                             or os.path.expanduser("~/.engine-ai"))
        self.config = config or Config()
        self.assignments = assignments or Assignments(self.home, self.config)
        self.limiter = limiter or RateLimiter()
        self.limiter.configure("team-primary", Limits(
            requests_per_minute=int(os.environ.get("ENGINE_AI_RPM", 20)),
            requests_per_day=int(os.environ.get("ENGINE_AI_RPD", 50))))
        self.provider_factory = provider_factory or self._default_provider
        self.registry = registry or build_registry()  # read/write/list/run — real tools
        self.verifier = verifier or Verifier()
        self.usage_totals = {"calls": 0, "input_tokens": 0, "output_tokens": 0}

    # ---------------------------------------------------------------- providers
    def _default_provider(self, profile, api_key: str) -> OpenRouterProvider:
        return OpenRouterProvider(api_key=api_key, limiter=self.limiter,
                                  account_group=profile.account_group)

    def _provider_for(self, profile_name: str):
        profile = self.assignments.profile(profile_name)
        key = self.assignments.resolve_key(profile)
        if not key:
            raise PipelineError(f"profile {profile_name!r} has no API key — import keys "
                                "first (engine-ai runtime keys, or openrouter_import)")
        return profile, self.provider_factory(profile, key)

    def _note_usage(self, completion) -> None:
        self.usage_totals["calls"] += 1
        self.usage_totals["input_tokens"] += completion.usage.input_tokens
        self.usage_totals["output_tokens"] += completion.usage.output_tokens

    # ---------------------------------------------------------------- plan
    def plan(self, objective: str, max_tasks: int = 6) -> dict:
        """Planner agent (its own key/model) ideates → validated TaskGraph."""
        profile, provider = self._provider_for("planner")
        completion = provider.complete(
            self.assignments.profile("planner").model_ref,
            [Message("system", PLANNER_SYSTEM), Message("user", objective)],
            max_output_tokens=profile.max_output_tokens)
        self._note_usage(completion)
        raw = extract_json(completion.content)
        specs = raw.get("tasks") or []
        if not isinstance(specs, list) or not specs:
            raise PipelineError(f"planner returned no tasks: {json.dumps(raw)[:300]}")
        if len(specs) > max_tasks:
            specs = specs[:max_tasks]
        graph = TaskGraph()
        for t in specs:
            if not isinstance(t, dict) or not t.get("id"):
                raise PipelineError(f"planner task missing id: {json.dumps(t)[:200]}")
            t.setdefault("profile", "backend")
            if t["profile"] not in self.assignments.profiles:
                t["profile"] = "backend"  # route unknown profiles somewhere safe
            graph.add(TaskSpec.from_dict(t))
        graph.validate()  # cycles / unknown deps — fail BEFORE spending worker quota
        return {"graph": graph, "plan_model": completion.model,
                "tasks": graph.topological_order()}  # ids, dependencies-resolved

    # ---------------------------------------------------------------- execute
    def execute(self, graph: TaskGraph, workspace: str, store: StateStore | None = None,
                run_verification: bool = True) -> dict:
        """Run the graph wave by wave; each task runs under ITS profile's key+model."""
        os.makedirs(workspace, exist_ok=True)
        store = store or StateStore(os.path.join(self.home, "runtime.db"))
        for spec in graph.tasks.values():
            spec.base_sha = spec.base_sha  # planner graphs start uncommitted
            store.upsert(spec)
        store.mark_ready(store.ready_ids())
        results, evidence = [], []
        executed: set[str] = set()
        while len(executed) < len(graph.tasks):
            ready = [tid for tid in store.ready_ids() if tid not in executed]
            if not ready:
                break
            for tid in ready:  # sequential waves: free-tier budgets are small
                spec = graph.tasks[tid]
                try:
                    profile, provider = self._provider_for(spec.profile)
                except PipelineError as e:
                    store.fail(tid, fence=store.get(tid)["fence"] or 0, error=str(e),
                               blocked=True)
                    results.append({"task": tid, "status": "blocked", "error": str(e)})
                    executed.add(tid)
                    continue
                packet = ContextPacket.build(spec, spans=[], budget_tokens=profile.input_target_tokens)
                assignment = store.claim(tid, owner=f"pipeline-{spec.profile}")
                executor = Executor(self.registry,
                                    Policy(read_only=profile.read_only,
                                           permitted_paths=spec.owned_paths or None,
                                           root=workspace),
                                    journal_path=os.path.join(self.home, "effects.db"))
                worker = Worker(provider, profile.model_ref, executor, store,
                                self.config, profile)
                outcome = worker.run(assignment, packet.render())
                self.usage_totals["calls"] += outcome.get("calls", 0)
                if run_verification and spec.verification and \
                        outcome.get("status") == "done":
                    run = self.verifier.run_checks(
                        workspace, [c.split() for c in spec.verification if c])
                    manifest = EvidenceManifest.from_run(tid, run)
                    evidence.append({"task": tid, "passed": run["passed"],
                                     "checks": run["checks"], "manifest": manifest.manifest_hash()})
                    if not run["passed"]:
                        outcome["status"] = "verification-failed"
                results.append(outcome)
                executed.add(tid)
        return {"results": results, "evidence": evidence,
                "usage": self.usage_totals,
                "complete": sum(1 for r in results if r.get("status") == "done")}

    # ---------------------------------------------------------------- all-in-one
    def run(self, objective: str, workspace: str | None = None, max_tasks: int = 6) -> dict:
        if not objective or not objective.strip():
            raise PipelineError("an objective is required — what should the agents build?")
        workspace = workspace or os.path.join(
            self.home, "builds", re.sub(r"[^a-z0-9]+", "-", objective.lower()).strip("-")[:40]
            or f"build-{int(time.time())}")
        plan = self.plan(objective, max_tasks=max_tasks)
        run = self.execute(plan["graph"], workspace)
        run.update({"objective": objective, "workspace": workspace,
                    "plan": plan["tasks"], "plan_model": plan["plan_model"]})
        return run
