"""TaskSpec + dependency graph (upgrade.md §5).

A TaskSpec is the small task packet from the review: objective, acceptance criteria,
permitted paths, base SHA, contracts, profile, budgets. The graph validates unknown
dependencies and cycles BEFORE anything is scheduled.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict


STATES = ("pending", "ready", "leased", "running", "verifying", "blocked", "failed", "complete")


class GraphError(ValueError):
    pass


@dataclass
class TaskSpec:
    id: str
    title: str = ""
    project: str = "default"
    profile: str = "backend"
    dependencies: list[str] = field(default_factory=list)
    base_sha: str | None = None
    owned_paths: list[str] = field(default_factory=list)
    shared_contract_version: str | None = None
    acceptance: list[str] = field(default_factory=list)
    verification: list[str] = field(default_factory=list)  # commands, run by the Verifier
    budgets: dict = field(default_factory=dict)  # {turns, calls, output_tokens, seconds, usd}
    objective: str = ""

    def __post_init__(self):
        if not self.id or not isinstance(self.id, str):
            raise GraphError("task id must be a non-empty string")
        self.dependencies = list(dict.fromkeys(self.dependencies or []))
        if self.id in self.dependencies:
            raise GraphError(f"task {self.id!r} depends on itself")

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "TaskSpec":
        known = {f for f in TaskSpec.__dataclass_fields__}
        return TaskSpec(**{k: v for k, v in d.items() if k in known})


class TaskGraph:
    def __init__(self):
        self.tasks: dict[str, TaskSpec] = {}

    def add(self, spec: TaskSpec) -> TaskSpec:
        self.tasks[spec.id] = spec
        return spec

    def validate(self) -> None:
        for spec in self.tasks.values():
            for dep in spec.dependencies:
                if dep not in self.tasks:
                    raise GraphError(f"task {spec.id!r} depends on unknown task {dep!r}")
        # DFS cycle check
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {tid: WHITE for tid in self.tasks}

        def visit(tid: str, path: list[str]):
            color[tid] = GRAY
            for dep in self.tasks[tid].dependencies:
                if color[dep] == GRAY:
                    raise GraphError("dependency cycle: " + " -> ".join(path + [tid, dep]))
                if color[dep] == WHITE:
                    visit(dep, path + [tid])
            color[tid] = BLACK

        for tid in self.tasks:
            if color[tid] == WHITE:
                visit(tid, [])

    def dependencies_of(self, task_id: str) -> list[str]:
        return list(self.tasks[task_id].dependencies)

    def dependents_of(self, task_id: str) -> list[str]:
        return [t.id for t in self.tasks.values() if task_id in t.dependencies]

    def ready(self, complete: set[str]) -> list[str]:
        """IDs whose dependencies are all complete — schedulable now."""
        self.validate()
        return [tid for tid, t in self.tasks.items()
                if tid not in complete and all(d in complete for d in t.dependencies)]

    def topological_order(self) -> list[str]:
        self.validate()
        out: list[str] = []
        done: set[str] = set()
        while len(out) < len(self.tasks):
            wave = [tid for tid in self.tasks
                    if tid not in done and all(d in done for d in self.tasks[tid].dependencies)]
            if not wave:
                raise GraphError("unsolvable graph")
            out.extend(sorted(wave))
            done.update(wave)
        return out
