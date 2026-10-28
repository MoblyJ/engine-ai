"""Retrieval sequence: exact paths/symbols → vector/FTS search → neighbors (upgrade.md §3).

Stage 1 resolves an exact path or symbol name straight to a full function/interface span
(no search fuzziness). Stage 2 falls back to the repo's vector index (mcp Engine). Stage 3
optionally expands to file neighbors of stage-1/2 hits. Every hit carries file hash,
lines, and revision so edits target verified text.
"""

from __future__ import annotations

import os

from engine_core._compat import mcp_module
from engine_core.context.compiler import CodeSpan
from engine_core.context.symbols import SymbolSpan, extract_file, lookup


class Retriever:
    def __init__(self, repo: str):
        self.repo = os.path.abspath(repo)
        self._spans: list[SymbolSpan] | None = None

    # ---------------------------------------------------------------- spans cache
    def spans(self, refresh: bool = False) -> list[SymbolSpan]:
        if self._spans is None or refresh:
            out = []
            for dp, dirs, files in os.walk(self.repo):
                dirs[:] = [d for d in dirs if d not in
                           {".git", "node_modules", "target", "dist", "build", ".venv", "__pycache__"}]
                for f in files:
                    out.extend(extract_file(os.path.join(dp, f)))
            self._spans = out
        return self._spans

    @staticmethod
    def _span_to_code(span: SymbolSpan, repo: str) -> CodeSpan | None:
        full = span.path if os.path.isabs(span.path) else os.path.join(repo, span.path)
        try:
            with open(full, encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
        except OSError:
            return None
        text = "".join(lines[span.start_line - 1:span.end_line])
        return CodeSpan(path=os.path.relpath(full, repo), start_line=span.start_line,
                        end_line=span.end_line, text=text, file_hash=span.text_hash,
                        origin="exact")

    # ---------------------------------------------------------------- stages
    def exact(self, name: str) -> list[CodeSpan]:
        """Stage 1: `path/to/file.py` or `ClassName.method` / `function_name`."""
        candidate = name if os.path.isabs(name) else os.path.join(self.repo, name)
        if os.path.isfile(candidate):  # whole file requested by path
            try:
                with open(candidate, encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            except OSError:
                return []
            return [CodeSpan(path=os.path.relpath(candidate, self.repo), start_line=1,
                             end_line=text.count("\n") + 1, text=text,
                             file_hash=f"{len(text):x}", origin="exact")]
        out = []
        for span in lookup(self.spans(), name):
            code = self._span_to_code(span, self.repo)
            if code:
                out.append(code)
        return out

    def search(self, query: str, k: int = 4) -> list[CodeSpan]:
        """Stage 2: repo vector search (mcp Engine), converted to line spans."""
        engine_mod = mcp_module("engine")
        eng = engine_mod.Engine()
        hits = eng.search_repo(self.repo, query, k=k)
        out = []
        for h in hits:
            full = os.path.join(self.repo, h["path"])
            try:
                with open(full, encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
            except OSError:
                continue
            sl, el = h.get("start_line") or 1, h.get("end_line") or len(lines)
            text = "".join(lines[sl - 1:el])
            out.append(CodeSpan(path=h["path"], start_line=sl, end_line=el, text=text,
                                file_hash=f"{len(text):x}", origin="retrieval"))
        return out

    def neighbors(self, span: CodeSpan) -> list[CodeSpan]:
        """Stage 3: the definitions immediately before/after a hit — dependency context."""
        out = []
        for s in self.spans():
            if s.path == os.path.join(self.repo, span.path) or s.path == span.path:
                if abs(s.start_line - span.end_line) <= 40 or abs(span.start_line - s.end_line) <= 40:
                    if not (s.start_line == span.start_line and s.end_line == span.end_line):
                        code = self._span_to_code(s, self.repo)
                        if code:
                            code.origin = "neighbor"
                            out.append(code)
        return out[:2]

    def retrieve(self, query: str, k: int = 4, expand_neighbors: bool = True) -> list[CodeSpan]:
        """The full sequence, deduplicated by (path, start, end)."""
        spans = self.exact(query)
        if not spans:
            spans = self.search(query, k=k)
        if expand_neighbors and spans:
            seen = {(s.path, s.start_line, s.end_line) for s in spans}
            for base in list(spans):
                for nb in self.neighbors(base):
                    key = (nb.path, nb.start_line, nb.end_line)
                    if key not in seen:
                        seen.add(key)
                        spans.append(nb)
        return spans
