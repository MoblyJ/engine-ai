"""StackOverflow / Stack Exchange API client for engine-ai — real-world Q&A for debugging
(error message → likely fix, cited) and code-design precedent (how others structured a
similar problem).

Uses the public Stack Exchange API (api.stackexchange.com/2.3). An API key raises the
per-IP request quota — store one with `set_secret({"name": "STACKOVERFLOW_API_KEY", "value":
"<key>"})`; reuses the EXISTING encrypted secrets vault (engine.py), no new storage
mechanism. Works without a key too, at a lower quota. Pure stdlib (urllib + gzip), no
third-party dependencies, matching the rest of engine-ai.
"""

from __future__ import annotations

import gzip
import html
import json
import re
import urllib.parse
import urllib.request

API_BASE = "https://api.stackexchange.com/2.3"

_CODE_BLOCK_RE = re.compile(r"<pre[^>]*><code>(.*?)</code></pre>", re.S)
_INLINE_CODE_RE = re.compile(r"<code>(.*?)</code>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_ERROR_LINE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*(Error|Exception)\b")


def _strip_html(body: str) -> str:
    """Answer bodies come back as HTML; convert to plain-text-with-markdown-code-fences
    so an agent can read/cite them without an HTML parser dependency."""
    text = _CODE_BLOCK_RE.sub(lambda m: "\n```\n" + html.unescape(m.group(1)) + "\n```\n", body)
    text = _INLINE_CODE_RE.sub(lambda m: "`" + html.unescape(m.group(1)) + "`", text)
    text = _TAG_RE.sub("", text)
    return html.unescape(text).strip()


def _extract_error_signature(error_text: str) -> str:
    """Pull the most search-worthy line out of a traceback/error dump: the last
    'ExceptionType: message' line, or the first non-empty line if none matches."""
    lines = [ln.strip() for ln in error_text.strip().splitlines() if ln.strip()]
    for line in reversed(lines):
        if _ERROR_LINE_RE.match(line):
            return line[:250]
    return lines[0][:250] if lines else error_text[:250]


_LANGUAGE_SIGNATURES = (
    ("python", re.compile(r"Traceback \(most recent call last\)|^\s*File \"", re.M)),
    ("javascript", re.compile(r"at Object\.<anonymous>|node:internal|ReferenceError|TypeError:.*undefined")),
    ("java", re.compile(r"\.java:\d+\)|Exception in thread")),
    ("go", re.compile(r"goroutine \d+ \[|\.go:\d+")),
    ("rust", re.compile(r"thread '.*' panicked at|\.rs:\d+")),
)


def _detect_language(error_text: str) -> str | None:
    """Best-effort language guess from common traceback/stack-trace formats, used to tag
    the search for better relevance. Returns None (no tag, unfiltered search) when unsure —
    never guesses wrong on purpose, since a wrong tag would filter out the real answer."""
    for lang, pattern in _LANGUAGE_SIGNATURES:
        if pattern.search(error_text):
            return lang
    return None


class StackOverflowQuestion(dict):
    """One question hit: title, link, score, answer_count, tags, question_id, is_answered."""

    def __init__(self, title: str, link: str, score: int, answer_count: int,
                 tags: list[str], question_id: int, is_answered: bool):
        super().__init__(title=html.unescape(title), link=link, score=score, answer_count=answer_count,
                          tags=tags, question_id=question_id, is_answered=is_answered)


class StackOverflowAnswer(dict):
    """One answer: body (plain text), score, is_accepted, link."""

    def __init__(self, body: str, score: int, is_accepted: bool, link: str):
        super().__init__(body=body, score=score, is_accepted=is_accepted, link=link)


class StackOverflowClient:
    """Thin, class-based wrapper over the Stack Exchange API. `get_secret` is injected
    (same dependency-injection pattern as WebSearch's Brave key) so this module has zero
    dependency on the secrets vault's storage details — only "give me a string or None"."""

    def __init__(self, get_secret=None, site: str = "stackoverflow"):
        self._get_secret = get_secret or (lambda name, scope="global": None)
        self.site = site

    def _get(self, path: str, params: dict) -> dict:
        merged = {**params, "site": self.site}
        api_key = self._get_secret("STACKOVERFLOW_API_KEY")
        if api_key:
            merged["key"] = api_key
        url = f"{API_BASE}/{path}?" + urllib.parse.urlencode(merged)
        req = urllib.request.Request(url, headers={"User-Agent": "engine-ai/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 — stdlib only, by design
            raw = r.read()
        try:
            raw = gzip.decompress(raw)  # the SE API gzips responses regardless of Accept-Encoding
        except OSError:
            pass
        data = json.loads(raw)
        if data.get("error_message"):
            raise RuntimeError(f"Stack Exchange API error: {data['error_message']}")
        return data

    def search_questions(self, query: str, tagged: str | list[str] | None = None, k: int = 5) -> list[StackOverflowQuestion]:
        # `q` (free-text search, what stackoverflow.com's own search box uses) is far more
        # relevant here than `intitle` (strict title-phrase matching) — verified live: intitle
        # on a full error message returned unrelated top-voted questions as a fallback, while
        # `q` correctly surfaced the actual matching questions.
        params = {"order": "desc", "sort": "relevance", "q": query, "pagesize": k}
        if tagged:
            params["tagged"] = tagged if isinstance(tagged, str) else ";".join(tagged)
        items = self._get("search/advanced", params).get("items", [])
        return [StackOverflowQuestion(i.get("title", ""), i.get("link", ""), i.get("score", 0),
                                      i.get("answer_count", 0), i.get("tags", []), i.get("question_id"),
                                      i.get("is_answered", False))
                for i in items[:k]]

    def top_answer(self, question_id: int) -> StackOverflowAnswer | None:
        items = self._get(f"questions/{question_id}/answers",
                          {"order": "desc", "sort": "votes", "pagesize": 1, "filter": "withbody"}).get("items", [])
        if not items:
            return None
        a = items[0]
        return StackOverflowAnswer(_strip_html(a.get("body", "")), a.get("score", 0),
                                   a.get("is_accepted", False), f"https://stackoverflow.com/a/{a['answer_id']}")

    def debug(self, error_text: str, language: str | None = None, k: int = 5) -> list[dict]:
        """High-level: given an error message/traceback, find the most relevant questions +
        each one's top answer — ready to hand to a debugging agent, citations included.
        Auto-tags by language when detectable (verified live: tagging measurably improves
        relevance over an untagged search of the same error text) — pass `language` to
        override when it can't be inferred from the traceback format."""
        signature = _extract_error_signature(error_text)
        tag = language or _detect_language(error_text)
        questions = self.search_questions(signature, tagged=tag, k=k)
        results = []
        for q in questions:
            answer = self.top_answer(q["question_id"]) if q["is_answered"] else None
            results.append({**q, "top_answer": answer})
        return results
