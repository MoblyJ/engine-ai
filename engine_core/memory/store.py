"""Project-scoped memory facade + migration helper (upgrade.md §3).

Memory categories are SEPARATE, per the review: verified facts, decisions, proposed
ideas, temporary errors. A model claim is not verified evidence — record it as `idea`
until a check proves it, then supersede with a `fact`. Cross-project recall is
structurally impossible: every read/write pins the project.
"""

from __future__ import annotations

from engine_core._compat import mcp_module


class ScopedMemory:
    def __init__(self, project: str, memory=None):
        self.project = project
        self._m = memory or mcp_module("memory").Memory()

    # ---------------------------------------------------------------- writes
    def fact(self, keywords, text, data=None, source=None):
        return self._m.save(keywords, text, data, kind="fact", project=self.project, source=source)

    def decision(self, keywords, text, data=None, source=None, supersedes=None):
        return self._m.save(keywords, text, data, kind="decision", project=self.project,
                            source=source, supersedes=supersedes)

    def idea(self, keywords, text, data=None):
        return self._m.save(keywords, text, data, kind="idea", project=self.project)

    def error(self, keywords, text, data=None):
        return self._m.save(keywords, text, data, kind="error", project=self.project)

    # ---------------------------------------------------------------- reads
    def recall(self, keywords, k=3):
        return self._m.recall(keywords, k=k, project=self.project)

    def verified_decisions(self, keywords, k=5):
        """Only kind=decision pockets — what was DECIDED, not what was mused."""
        rec = self.recall(keywords, k=k)
        rec["pockets"] = [p for p in rec["pockets"] if p["kind"] == "decision"]
        return rec

    def verified_facts(self, keywords, k=5):
        rec = self.recall(keywords, k=k)
        rec["pockets"] = [p for p in rec["pockets"] if p["kind"] == "fact"]
        return rec

    def get(self, pocket_id):
        out = self._m.get(pocket_id)
        if out.get("ok") and out.get("project") != self.project:
            return {"ok": False, "error": f"pocket #{pocket_id} belongs to project {out['project']!r}"}
        return out

    def supersede(self, old_id, keywords, text, data=None, source=None):
        """Explicitly retire an outdated decision/fact with its replacement."""
        return self._m.save(keywords, text, data, kind="decision", project=self.project,
                            source=source, supersedes=old_id)
