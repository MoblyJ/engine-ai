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
from engine_core.providers.catalog import CatalogError  # noqa: E402
from engine_core.providers.limiter import Limits, RateLimiter  # noqa: E402
from engine_core.providers.openrouter import OpenRouterProvider  # noqa: E402
from engine_core.runtime.scheduler import QuotaPaused, Scheduler  # noqa: E402
from engine_core.runtime.state import StateError, StateStore  # noqa: E402
from engine_core.runtime.task_graph import GraphError, TaskGraph, TaskSpec  # noqa: E402
from engine_core.runtime.worker import Worker  # noqa: E402
from engine_core.telemetry.usage import UsageJournal  # noqa: E402
from engine_core.tools.executor import Executor  # noqa: E402
from engine_core.tools.policy import Policy  # noqa: E402
from engine_core.tools.registry import default_registry  # noqa: E402
from engine_core.verification.runner import Verifier  # noqa: E402

HOME = os.environ.get("ENGINE_AI_HOME") or os.path.expanduser("~/.engine-ai")


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
    from engine_core.profiles import Assignments
    config = load_config(args.config) if args.config else Config()
    # no config file → the ten built-in profiles (with their env credential refs);
    # a config file's profiles replace/extend them
    assignments = Assignments(HOME, config if args.config else None)
    # --agent maps to that agent's profile; --profile names a profile directly
    if args.agent:
        profile = assignments.profile_for_agent(args.agent)
    else:
        profile = assignments.profile(args.profile)
    # limits are account facts, not code constants: defaults reflect OpenRouter's
    # published free tier at review time; override per account via env vars.
    limiter = RateLimiter()
    limiter.configure(profile.account_group, Limits(
        requests_per_minute=int(os.environ.get("ENGINE_AI_RPM", 20)),
        requests_per_day=int(os.environ.get("ENGINE_AI_RPD", 50))))
    api_key = assignments.resolve_key(profile)
    if not api_key:
        env_name = "OPENROUTER_" + profile.name.upper().replace("-", "_") + "_KEY"
        print(f"✗ profile {profile.name!r} has no API key configured "
              f"(credential_ref {profile.credential_ref!r}).\n"
              f"  export {env_name}=…  — or store it encrypted and reference it:\n"
              f"    engine-ai runtime profiles set {profile.name} "
              f"--credential-ref vault:{env_name}\n"
              f"    then store the key itself with the set_secret MCP tool "
              f"(name {env_name})", file=sys.stderr)
        return 1
    catalog = _catalog()
    model_info = catalog.resolve(args.model or profile.model_ref)
    if args.free_only and not model_info.free:
        print(f"✗ {model_info.id} is not free and routing is free-only", file=sys.stderr)
        return 1
    provider = OpenRouterProvider(api_key=api_key,
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


def cmd_profiles(args) -> int:
    """Per-agent API-key/model assignment: list / show / set / clear / agents."""
    from engine_core.profiles import Assignments
    config = load_config(args.config) if getattr(args, "config", None) else None
    a = Assignments(HOME, config)

    if args.profiles_cmd == "list":
        rows = a.status(catalog=_catalog())
        print(f"{'profile':16} {'key':16} {'credential_ref':38} {'model':24} agents")
        for r in rows:
            model = r.get("model", r["model_ref"])
            agents = ",".join(r["agents"]) or "-"
            print(f"{r['profile']:16} {r['key']:16} {r['credential_ref']:38} {str(model)[:24]:24} {agents}")
        missing = [r["profile"] for r in rows if r["key"] == "MISSING"]
        if missing:
            print(f"\n{len(missing)} profile(s) without a key: {', '.join(missing)}")
            print("assign one:  engine-ai runtime profiles set <profile> "
                  "--credential-ref vault:OPENROUTER_<PROFILE>_KEY --model <id>")
        return 0
    if args.profiles_cmd == "show":
        from engine_core.profiles import mask
        p = a.profile(args.profile)
        print(json.dumps({"name": p.name, "provider": p.provider,
                          "credential_ref": p.credential_ref,
                          "key": mask(a.resolve_key(p)),
                          "model_ref": p.model_ref,
                          "account_group": p.account_group, "read_only": p.read_only,
                          "input_target_tokens": p.input_target_tokens,
                          "max_output_tokens": p.max_output_tokens,
                          "agents": sorted(ag for ag, pr in a.agents.items() if pr == p.name)},
                         indent=2))
        return 0
    if args.profiles_cmd == "set":
        p = a.set(args.profile, credential_ref=args.credential_ref,
                  model_ref=args.model, read_only=args.read_only,
                  account_group=args.account_group)
        print(f"profile {p.name!r}: credential_ref={p.credential_ref} model_ref={p.model_ref}"
              + (" read_only" if p.read_only else ""))
        print("(references only — the key itself stays in the env var or the vault; "
              "store it with set_secret, never in shared files)")
        return 0
    if args.profiles_cmd == "clear":
        print(f"cleared overrides for {args.profile!r}" if a.clear(args.profile)
              else f"no overrides for {args.profile!r}")
        return 0
    # agents: engine agents + domain experts -> profiles
    print("engine agents:")
    for agent, profile in sorted(a.agents.items()):
        print(f"  {agent:24} → {profile}")
    print("domain experts (via suggest_experts routing):")
    for agent, profile in sorted(a.domain_agent_profiles().items()):
        print(f"  {agent:24} → {profile}")
    return 0


def cmd_models(args) -> int:
    catalog = _catalog()
    if args.models_cmd == "refresh":
        try:
            n = catalog.refresh()
        except Exception as e:  # noqa: BLE001 - network failure → clean CLI error
            print(f"✗ catalog refresh failed: {e}", file=sys.stderr)
            return 1
        print(f"refreshed {n} models (age resets; cached at {catalog.path})")
        return 0
    if args.models_cmd == "pin":
        try:
            catalog.pin(args.name, args.model_id)
        except CatalogError as e:
            print(f"✗ {e}", file=sys.stderr)
            return 1
        print(f"pinned {args.name!r} → {args.model_id}")
        return 0
    # list: eligible models given the routing constraints
    models = catalog.eligible(free_only=not args.include_paid,
                              supports_tools=not args.all_capabilities)
    if not models:
        print("(no models cached — run: engine-ai runtime models refresh)")
        return 0
    for m in models[: int(args.limit)]:
        tags = ("free" if m.free else f"${m.pricing_prompt:g}/M") + (", tools" if m.supports_tools else "")
        print(f"  {m.id:48} ctx={m.context_length or '?':>8}  {tags}")
    return 0


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
    p.add_argument("--profile", default="backend",
                   help="run under this profile (its key + model)")
    p.add_argument("--agent", help="run under an agent's assigned profile "
                                   "(e.g. engine-app-builder)")
    p.add_argument("--model", help="override the profile's model ref")
    p.add_argument("--free-only", action="store_true", default=False)
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("verify", help="run verification commands → evidence")
    p.add_argument("--path", required=True)
    p.add_argument("command", help="command(s) to run; split '&&' into sequential checks")
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("profiles", help="per-agent API key + model assignments")
    p.add_argument("--config", help="base engine config (YAML/JSON)")
    pr_sub = p.add_subparsers(dest="profiles_cmd", required=True)
    pr_sub.add_parser("list", help="all profiles: key status (masked), model, agents")
    ps = pr_sub.add_parser("show", help="one profile in full")
    ps.add_argument("profile")
    pset = pr_sub.add_parser("set", help="assign a credential ref / model to a profile")
    pset.add_argument("profile")
    pset.add_argument("--credential-ref", help="env:VAR or vault:NAME (references only)")
    pset.add_argument("--model", help="model id or catalog:<name> ref")
    pset.add_argument("--read-only", dest="read_only", action="store_true", default=None)
    pset.add_argument("--account-group")
    pcl = pr_sub.add_parser("clear", help="remove local overrides for a profile")
    pcl.add_argument("profile")
    pr_sub.add_parser("agents", help="agent → profile mapping (engine + domain experts)")
    p.set_defaults(fn=cmd_profiles)

    p = sub.add_parser("models", help="model catalog: list / refresh / pin")
    m_sub = p.add_subparsers(dest="models_cmd", required=True)
    pl = m_sub.add_parser("list", help="list eligible models (free-only by default)")
    pl.add_argument("--include-paid", action="store_true")
    pl.add_argument("--all-capabilities", action="store_true",
                    help="don't require tool support")
    pl.add_argument("--limit", default=25)
    m_sub.add_parser("refresh", help="fetch the live catalog (network)")
    pp = m_sub.add_parser("pin", help="pin a catalog:<name> reference to a model ID")
    pp.add_argument("name")
    pp.add_argument("model_id")
    p.set_defaults(fn=cmd_models)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (ConfigError, GraphError, CatalogError, StateError) as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1
    except ProviderError as e:
        print(f"✗ provider error ({e.kind}): {e.message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
