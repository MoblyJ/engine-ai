"""engine_core runtime CLI (upgrade.md §7 — the proposed future CLI, first slice).

  python3 -m engine_core status                     task states + quota + usage totals
  python3 -m engine_core plan --graph tasks.json    validate a task graph (cycles, unknown deps)
  python3 -m engine_core run --config engine.yaml --graph tasks.json [--free-only]
                                                       lease + execute ready tasks via a provider
  python3 -m engine_core verify --path DIR -- cmd...   run verification commands → evidence

`connect`/`doctor`/`knowledge` stay in the existing engine-ai CLI (preserved).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine_core.config import Config, ConfigError, load_config  # noqa: E402
from engine_core.providers.base import ProviderError  # noqa: E402
from engine_core.providers.limiter import RateLimiter  # noqa: E402
from engine_core.providers.openrouter import OpenRouterProvider  # noqa: E402
from engine_core.runtime.scheduler import QuotaPaused, Scheduler  # noqa: E402
from engine_core.runtime.state import StateStore  # noqa: E402
from engine_core.runtime.task_graph import GraphError, TaskGraph, TaskSpec  # noqa: E402
from engine_core.runtime.worker import Worker  # noqa: E402
from engine_core.telemetry.usage import UsageJournal  # noqa: E402
from engine_core.tools.executor import Executor  # noqa: E402
from engine_core.tools.policy import Policy  # noqa: E402
from engine_core.tools.registry import default_registry  # noqa: E402
from engine_core.verification.runner import Verifier  # noqa: E402

HOME = os.environ.get("ENGINE_AI_HOME", os.path.expanduser("~/.engine-ai"))


def _store() -> StateStore:
    os.makedirs(HOME, exist_ok=True)
    return StateStore(os.path.join(HOME, "runtime.db"))


def _load_graph(path: str) -> TaskGraph:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    graph = TaskGraph()
    for t in data.get("tasks", data if isinstance(data, list) else []):
        graph.add(TaskSpec.from_dict(t))
    graph.validate()
    return graph


def cmd_status(args) -> int:
    store = _store()
    sched = Scheduler(store, load_runtime_config(args))
    print(json.dumps(sched.status(), indent=2))
    usage_path = os.path.join(HOME, "usage.db")
    if os.path.exists(usage_path):
        print(json.dumps(UsageJournal(usage_path).totals(), indent=2))
    return 0


def load_runtime_config(args) -> Config:
    if getattr(args, "config", None) and os.path.exists(args.config):
        return load_config(args.config)
    return Config()


def cmd_plan(args) -> int:
    graph = _load_graph(args.graph)
    print("graph OK — topological order:")
    for tid in graph.topological_order():
        t = graph.tasks[tid]
        deps = ", ".join(t.dependencies) or "-"
        print(f"  {tid:24} profile={t.profile:12} deps: {deps}")
    return 0


def cmd_run(args) -> int:
    config = load_config(args.config) if args.config else Config()
    graph = _load_graph(args.graph)
    store = _store()
    for spec in graph.tasks.values():
        store.upsert(spec)
    store.mark_ready(store.ready_ids())

    profile = config.profile(args.profile)
    limiter = RateLimiter()
    limiter.configure(profile.account_group)
    catalog = _catalog()
    model_info = catalog.resolve(args.model or profile.model_ref)
    if args.free_only and not model_info.free:
        print(f"✗ {model_info.id} is not free and routing is free-only", file=sys.stderr)
        return 1
    provider = OpenRouterProvider(credential_ref=profile.credential_ref,
                                  limiter=limiter, account_group=profile.account_group)
    owned = [p for t in graph.tasks.values() for p in t.owned_paths]
    executor = Executor(default_registry(),
                        Policy(read_only=profile.read_only,
                               permitted_paths=owned or None),
                        journal_path=os.path.join(HOME, "effects.db"))
    usage = UsageJournal(os.path.join(HOME, "usage.db"))
    worker = Worker(provider, model_info.id, executor, store, config, profile, usage)
    sched = Scheduler(store, config, limiter)

    ran = 0
    try:
        while True:
            assignments = sched.next_assignments("cli", profile.account_group)
            if not assignments:
                break
            for a in assignments:
                spec = graph.tasks[a["task_id"]]
                packet = (f"Task {spec.id}: {spec.title or spec.objective}\n"
                          f"Acceptance: {spec.acceptance}\nVerification: {spec.verification}")
                outcome = worker.run(a, packet)
                print(json.dumps(outcome, indent=2)[:2000])
                ran += 1
                if outcome.get("status", "").startswith("provider-"):
                    print(f"✋ stopping: {outcome['status']} (checkpoint saved, resume later)",
                          file=sys.stderr)
                    return 2
    except QuotaPaused as e:
        print(f"⏸ quota paused: {e} (state checkpointed — re-run later)", file=sys.stderr)
        return 2
    finally:
        usage.close()
    print(f"ran {ran} task(s); status:")
    print(json.dumps(sched.status(), indent=2))
    return 0


def _catalog():
    from engine_core.providers.catalog import Catalog
    return Catalog()


def cmd_verify(args) -> int:
    verifier = Verifier()
    # run through bash so quoting/&& works as typed; segments split on &&
    commands = [["bash", "-c", c.strip()] for c in args.command.split("&&") if c.strip()]
    result = verifier.run_checks(args.path, commands, commit=_git_sha(args.path))
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


def _git_sha(path: str) -> str | None:
    import subprocess
    try:
        p = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, capture_output=True,
                           text=True, timeout=20)
        return p.stdout.strip() if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="engine-ai runtime", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("status", help="task states + quota + usage")
    p.add_argument("--config")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("plan", help="validate a task dependency graph")
    p.add_argument("--graph", required=True, help="JSON file: {tasks: [TaskSpec…]}")
    p.set_defaults(fn=cmd_plan)

    p = sub.add_parser("run", help="lease and execute ready tasks via a provider")
    p.add_argument("--config", help="engine config (YAML/JSON)")
    p.add_argument("--graph", required=True)
    p.add_argument("--profile", default="backend")
    p.add_argument("--model", help="override the profile's model ref")
    p.add_argument("--free-only", action="store_true", default=False)
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("verify", help="run verification commands → evidence")
    p.add_argument("--path", required=True)
    p.add_argument("command", help="command(s) to run; split '&&' into sequential checks")
    p.set_defaults(fn=cmd_verify)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (ConfigError, GraphError) as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1
    except ProviderError as e:
        print(f"✗ provider error ({e.kind}): {e.message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
