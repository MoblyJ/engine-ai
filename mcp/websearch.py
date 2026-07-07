"""Live web search for engine-ai — current, real-time information beyond the offline
knowledge store. `knowledge.py` retrieves from *ingested*, static engineering repos;
this retrieves from the *live web*, for anything time-sensitive: current docs, recent
releases, pricing, breaking changes, news.

Pluggable providers (Strategy pattern) so adding a new backend never touches call sites:
  - BraveSearchProvider — used when a `BRAVE_API_KEY` secret is set (via set_secret); best quality.
  - DuckDuckGoProvider  — no key required, always available as the fallback.

`WebSearch` picks the best available provider and falls back automatically. No third-party
dependencies — stdlib `urllib` only, matching the rest of engine-ai. Reuses the existing
encrypted secrets vault (engine.py) for the optional API key instead of a new storage
mechanism, and lets callers surface failures through the MCP server's existing generic
tool-error handling instead of inventing a parallel one.
"""

from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod


class SearchResult(dict):
    """One search hit: title, url, snippet. Dict-based so it's already JSON-serializable
    (MCP tool results are JSON) without a separate to_dict() step."""

    def __init__(self, title: str, url: str, snippet: str):
        super().__init__(title=title.strip(), url=url.strip(), snippet=snippet.strip())


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


class SearchProvider(ABC):
    """One live-search backend. Subclass + implement `search()` to add a new provider —
    `WebSearch` never needs to change."""

    name: str = "provider"

    @abstractmethod
    def search(self, query: str, k: int) -> list[SearchResult]:
        ...

    @staticmethod
    def _fetch(url: str, headers: dict | None = None, timeout: int = 10) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "engine-ai/1.0", **(headers or {})})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — stdlib only, by design
            return r.read()


class DuckDuckGoProvider(SearchProvider):
    """No API key needed — parses DuckDuckGo's plain-HTML results endpoint."""

    name = "duckduckgo"
    _RESULT_RE = re.compile(
        r'<a rel="nofollow" class="result__a" href="([^"]+)">(.*?)</a>.*?'
        r'<a class="result__snippet"[^>]*>(.*?)</a>',
        re.S,
    )
    _REDIRECT_RE = re.compile(r"uddg=([^&]+)")

    def search(self, query: str, k: int) -> list[SearchResult]:
        url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
        body = self._fetch(url).decode("utf-8", errors="ignore")
        out = []
        for href, title, snippet in self._RESULT_RE.findall(body):
            out.append(SearchResult(_strip_tags(html.unescape(title)), self._clean_url(href),
                                    _strip_tags(html.unescape(snippet))))
            if len(out) >= k:
                break
        return out

    @classmethod
    def _clean_url(cls, href: str) -> str:
        # DuckDuckGo's HTML results wrap the real URL in a redirect: //duckduckgo.com/l/?uddg=<encoded>
        m = cls._REDIRECT_RE.search(href)
        return urllib.parse.unquote(m.group(1)) if m else href


class BraveSearchProvider(SearchProvider):
    """Higher-quality results via the Brave Search API. Requires an API key — store one
    with `set_secret({"name": "BRAVE_API_KEY", "value": "<key>"})` and it's picked up
    automatically, no code/config changes needed."""

    name = "brave"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def search(self, query: str, k: int) -> list[SearchResult]:
        url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode({"q": query, "count": k})
        body = self._fetch(url, headers={"X-Subscription-Token": self.api_key, "Accept": "application/json"})
        hits = (json.loads(body).get("web") or {}).get("results") or []
        return [SearchResult(h.get("title", ""), h.get("url", ""), h.get("description", "")) for h in hits[:k]]


class WebSearch:
    """Facade over the available SearchProviders — picks the best one and falls back
    automatically. Reports status the same way integrations.py's gh_status()/vercel_status()
    do, so the UX is consistent across engine-ai's external integrations."""

    def __init__(self, get_secret=None):
        # `get_secret` is injected (defaults to "no key configured") rather than importing
        # Engine directly, so this module has zero dependency on the secrets vault's
        # storage details — it only needs "give me a string or None for this name".
        self._get_secret = get_secret or (lambda name, scope="global": None)

    @property
    def providers(self) -> list[SearchProvider]:
        # Rebuilt on every access (cheap: one dict lookup, no I/O) rather than cached, so
        # calling set_secret("BRAVE_API_KEY", ...) mid-session takes effect immediately —
        # a stale cached provider list would otherwise silently ignore a newly-added key.
        providers: list[SearchProvider] = []
        api_key = self._get_secret("BRAVE_API_KEY")
        if api_key:
            providers.append(BraveSearchProvider(api_key))
        providers.append(DuckDuckGoProvider())  # always available, no key needed
        return providers

    def status(self) -> dict:
        active = self.providers[0]
        return {
            "active_provider": active.name,
            "brave_key_set": any(isinstance(p, BraveSearchProvider) for p in self.providers),
            "hint": None if active.name == "brave" else
                    "set_secret({\"name\": \"BRAVE_API_KEY\", \"value\": \"<key>\"}) for higher-quality results",
        }

    def search(self, query: str, k: int = 5) -> list[dict]:
        errors = []
        for provider in self.providers:
            try:
                results = provider.search(query, k)
                if results:
                    return results
            except Exception as e:  # noqa: BLE001 — one provider failing must not break the fallback chain
                errors.append(f"{provider.name}: {e!r}")
        if errors:
            raise RuntimeError(f"all search providers failed: {'; '.join(errors)}")
        return []
