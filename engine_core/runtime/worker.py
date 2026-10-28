"""Worker loop — request → validated tool calls → bounded results → continuation → outcome.

Per upgrade.md §3-4:
- Each turn is bounded (turns/calls/output); every result is size-bounded before it
  re-enters model context.
- Tool effects are JOURNALED: after a transport failure the loop resumes from the
  checkpoint instead of blindly repeating writes.
- auth/quota provider errors stop the loop for escalation — never account rotation.
- The final outcome is STRUCTURED (status, changed files, checks, unresolved questions)
  and persisted outside the model context; full logs go to the event log, not prompts.
- Checkpoints after every turn make runs resumable (run → crash → resume RUN_ID).
"""

from __future__ import annotations

import json
import time

from engine_core.providers.base import Message, Provider, ProviderError
from engine_core.runtime.state import StateStore
from engine_core.tools.executor import Executor
from engine_core.telemetry.usage import UsageJournal

MAX_RESULT_CHARS = 8000  # bounded tool results before re-entering model context

SYSTEM_PROMPT = (
    "You are an engine-ai worker executing one bounded task packet. Work only within the "
    "permitted paths. Call tools as needed; when the task's acceptance criteria are met, "
    "reply with a JSON object on its own: "
    '{"status":"done"|"blocked","summary":"...","changed_files":[...],"checks":[...],'
    '"unresolved":[...]}. Otherwise continue working.'
)


class WorkerOutcome(dict):
    """Structured outcome: status, patch reference, changed files, checks, unresolved."""


class Worker:
    def __init__(self, provider: Provider, model: str, executor: Executor, store: StateStore,
                 config, profile, usage: UsageJournal | None = None, max_result_chars: int = MAX_RESULT_CHARS):
        self.provider = provider
        self.model = model
        self.executor = executor
        self.store = store
        self.config = config
        self.profile = profile
        self.usage = usage
        self.max_result_chars = max_result_chars

    # ---------------------------------------------------------------- tool defs
    def _tool_schemas(self) -> list[dict]:
        return [{"type": "function", "function": {
            "name": name,
            "description": (spec.get("description") or "")[:500],
            "parameters": spec.get("inputSchema") or {"type": "object", "properties": {}}}}
            for name, spec in self.executor.registry.schemas().items()
            if self.executor.policy.allows(name, self.executor.registry)]

    # ---------------------------------------------------------------- main loop
    def run(self, assignment: dict, packet_text: str, checkpoint: dict | None = None) -> WorkerOutcome:
        task_id, fence = assignment["task_id"], assignment["fence"]
        max_turns = self.profile.max_model_turns_per_task if hasattr(self.profile, "max_model_turns_per_task") \
            else self.config.max_model_turns_per_task
        max_calls = self.profile.budgets.get("calls", 40) if hasattr(self.profile, "budgets") else 40
        messages: list[Message] = [Message("system", SYSTEM_PROMPT), Message("user", packet_text)]
        calls_made = 0
        started = time.time()

        if checkpoint:  # resume: replay the journaled tool results, not the effects
            for entry in checkpoint.get("history", []):
                messages.append(Message(entry["role"], entry["content"]))
            calls_made = checkpoint.get("calls_made", 0)

        outcome: WorkerOutcome | None = None
        try:
            for turn in range(1, max_turns + 1):
                if not self.store.heartbeat(task_id, assignment["owner"], fence):
                    return WorkerOutcome({"status": "stale-lease", "task": task_id,
                                          "note": "a newer attempt owns this task"})
                completion = self.provider.complete(
                    self.model, messages, tools=self._tool_schemas(),
                    max_output_tokens=self.profile.max_output_tokens)
                if self.usage:
                    self.usage.record(task_id, completion.model, completion.usage)
                messages.append(Message("assistant", completion.content,
                                        tool_calls=list(completion.tool_calls)))
                if completion.tool_calls:
                    for call in completion.tool_calls[: max(0, max_calls - calls_made)]:
                        calls_made += 1
                        result = self.executor.execute(call, context={
                            "task_id": task_id,
                            "workspace": getattr(self.executor.policy, "root", None)})
                        messages.append(Message("tool", result["content"][:self.max_result_chars],
                                                tool_call_id=call.get("id")))
                    self.store.checkpoint(task_id, fence, {
                        "turn": turn, "calls_made": calls_made,
                        "history": [{"role": m.role, "content": m.content[:2000]}
                                    for m in messages[2:]], "model": self.model})
                    continue
                outcome = self._parse_outcome(completion.content, task_id)
                break
            if outcome is None:  # ran out of turns without a final answer
                outcome = WorkerOutcome({"status": "turn-budget-exhausted", "task": task_id,
                                         "turns": max_turns, "calls": calls_made})
        except ProviderError as e:
            if e.kind in (ProviderError.AUTH, ProviderError.QUOTA):
                # escalate, never rotate credentials or spin
                self.store.checkpoint(task_id, fence, {
                    "turn": -1, "calls_made": calls_made, "error": e.message,
                    "history": [{"role": m.role, "content": m.content[:2000]} for m in messages[2:]]})
                return WorkerOutcome({"status": f"provider-{e.kind}", "task": task_id,
                                      "error": e.message, "checkpointed": True})
            outcome = WorkerOutcome({"status": "provider-error", "task": task_id,
                                     "error": e.message, "kind": e.kind})
        duration = round(time.time() - started, 2)
        outcome["duration_s"] = duration
        outcome["calls"] = calls_made
        self.store.complete(task_id, fence, dict(outcome))
        return outcome

    # ---------------------------------------------------------------- outcome
    @staticmethod
    def _parse_outcome(text: str, task_id: str) -> WorkerOutcome:
        """Extract the structured outcome JSON; tolerate surrounding prose."""
        try:
            start = text.index("{")
            depth, end = 0, -1
            for i, ch in enumerate(text[start:], start):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            if end > 0:
                data = json.loads(text[start:end + 1])
                if isinstance(data, dict) and "status" in data:
                    return WorkerOutcome({**data, "task": task_id})
        except (ValueError, json.JSONDecodeError):
            pass
        return WorkerOutcome({"status": "done", "task": task_id, "summary": text[:2000],
                              "unparsed": True})
