"""Context compiler — bounded task packets (upgrade.md §3).

Each worker gets a task packet: objective, acceptance criteria, permitted paths, base
SHA, contracts, relevant source spans, verified decisions, latest failure. Not every
prior transcript. The illustrative 8k allocation from the review (1k instructions /
1k objective+contracts / 4k code / 1k decisions / 1k errors) is the DEFAULT; budgets
are adjustable per profile and expand only when missing evidence justifies it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


@dataclass
class CodeSpan:
    path: str
    start_line: int
    end_line: int
    text: str
    file_hash: str = ""
    origin: str = "retrieval"  # exact | retrieval | neighbor | manual

    def render(self) -> str:
        return f"--- {self.path}:{self.start_line}-{self.end_line} (hash {self.file_hash[:12]}) ---\n{self.text}"


@dataclass
class ContextPacket:
    task_id: str = ""
    objective: str = ""
    acceptance: list[str] = field(default_factory=list)
    permitted_paths: list[str] = field(default_factory=list)
    base_sha: str | None = None
    contracts: list[str] = field(default_factory=list)
    code_spans: list[CodeSpan] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)   # verified decisions only
    latest_failure: str = ""
    instructions: str = ""
    budget_tokens: int = 8192

    # section budgets as FRACTIONS of the token budget (review's illustrative 8k split)
    SECTIONS = {"instructions": 0.125, "objective": 0.125, "code": 0.5,
                "decisions": 0.125, "failure": 0.125}

    def render(self) -> str:
        budget_chars = self.budget_tokens * CHARS_PER_TOKEN

        def cap(section: str, text: str) -> str:
            limit = int(budget_chars * self.SECTIONS[section])
            return text if len(text) <= limit else text[:limit] + f"\n…({section} truncated to budget)"

        parts = [f"# Task packet {self.task_id} (budget {self.budget_tokens} tokens)"]
        if self.instructions:
            parts.append(cap("instructions", "## Instructions\n" + self.instructions))
        obj = "## Objective\n" + self.objective
        if self.acceptance:
            obj += "\nAcceptance criteria:\n" + "\n".join(f"- [ ] {a}" for a in self.acceptance)
        if self.permitted_paths:
            obj += "\nPermitted paths: " + ", ".join(self.permitted_paths)
        obj += f"\nBase commit: {self.base_sha or '(uncommitted)'}"
        if self.contracts:
            obj += "\nContracts (must honor):\n" + "\n".join(f"- {c}" for c in self.contracts)
        parts.append(cap("objective", obj))
        code = "## Relevant code\n" + "\n\n".join(s.render() for s in self.code_spans) \
            if self.code_spans else "## Relevant code\n(none retrieved)"
        parts.append(cap("code", code))
        if self.decisions:
            parts.append(cap("decisions", "## Verified decisions\n" +
                             "\n".join(f"- {d}" for d in self.decisions)))
        if self.latest_failure:
            parts.append(cap("failure", "## Latest failure\n" + self.latest_failure[-2000:]))
        return "\n\n".join(parts)

    def measured_tokens(self) -> int:
        return estimate_tokens(self.render())

    def content_hash(self) -> str:
        return hashlib.sha1(self.render().encode()).hexdigest()[:16]

    @staticmethod
    def build(task, spans: list[CodeSpan], decisions: list[str] | None = None,
              latest_failure: str = "", budget_tokens: int = 8192,
              instructions: str = "") -> "ContextPacket":
        return ContextPacket(task_id=task.id, objective=task.objective or task.title,
                             acceptance=task.acceptance, permitted_paths=task.owned_paths,
                             base_sha=task.base_sha, contracts=(
                                 [f"{task.shared_contract_version}"] if task.shared_contract_version else []),
                             code_spans=spans, decisions=decisions or [],
                             latest_failure=latest_failure, instructions=instructions,
                             budget_tokens=budget_tokens)
