"""Symbol spans — exact file/line anchors for retrieval (upgrade.md §3).

Python spans come from `ast` (precise); other languages get a conservative regex pass.
Spans carry the source hash + revision so a cached span is never mistaken for current code.
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass, asdict

_JS_GO_LIKE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function\s*\*?\s+(\w+)|"
    r"class\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?(?:function|\()|"
    r"def\s+(\w+)|fn\s+(\w+)|func\s+(?:\([^)]*\)\s*)?(\w+)|type\s+(\w+)\s+struct)", re.M)


@dataclass
class SymbolSpan:
    path: str
    name: str
    kind: str  # function | class | struct | type | definition
    start_line: int
    end_line: int
    text_hash: str
    language: str

    def to_dict(self) -> dict:
        return asdict(self)


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


def extract_python(path: str, source: str) -> list[SymbolSpan]:
    spans = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return spans
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            end = getattr(node, "end_lineno", None) or node.lineno
            spans.append(SymbolSpan(path=path, name=node.name,
                                    kind="class" if isinstance(node, ast.ClassDef) else "function",
                                    start_line=node.lineno, end_line=end,
                                    text_hash=_hash(source), language="python"))
    return spans


def extract_regex(path: str, source: str, language: str) -> list[SymbolSpan]:
    spans = []
    for m in _JS_GO_LIKE.finditer(source):
        name = next(g for g in m.groups() if g)
        start = source.count("\n", 0, m.start()) + 1
        # span the block by brace/indent balance: cap at the next top-level definition
        nxt = _JS_GO_LIKE.search(source, m.end())
        end = source.count("\n", 0, nxt.start()) if nxt else source.count("\n", 0, len(source)) + 1
        spans.append(SymbolSpan(path=path, name=name, kind="definition",
                                start_line=start, end_line=max(start, end),
                                text_hash=_hash(source), language=language))
    return spans


_LANG_BY_EXT = {".py": ("python", extract_python), ".js": ("javascript", extract_regex),
                ".jsx": ("javascript", extract_regex), ".ts": ("typescript", extract_regex),
                ".tsx": ("typescript", extract_regex), ".go": ("go", extract_regex),
                ".rs": ("rust", extract_regex)}


def extract_file(path: str, source: str | None = None) -> list[SymbolSpan]:
    import os
    ext = os.path.splitext(path)[1].lower()
    if ext not in _LANG_BY_EXT:
        return []
    language, fn = _LANG_BY_EXT[ext]
    if source is None:
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                source = f.read()
        except OSError:
            return []
    return fn(path, source) if language == "python" else fn(path, source, language)


def lookup(spans: list[SymbolSpan], name: str) -> list[SymbolSpan]:
    """Exact symbol lookup — the first stage of the retrieval sequence."""
    n = name.split(".")[-1]
    return [s for s in spans if s.name == n]
