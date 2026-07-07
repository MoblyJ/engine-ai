"""Web search — provider fallback, DuckDuckGo HTML parsing, Brave JSON parsing, no live network calls."""
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp"))
from websearch import BraveSearchProvider, DuckDuckGoProvider, SearchResult, WebSearch  # noqa: E402

DDG_HTML = (
    '<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">'
    'First &amp; Result</a><a class="result__snippet">A <b>snippet</b> here.</a>'
    '<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fb">'
    'Second Result</a><a class="result__snippet">Another snippet.</a>'
)

BRAVE_JSON = json.dumps({"web": {"results": [
    {"title": "Brave Result", "url": "https://brave.example/x", "description": "brave snippet"},
]}})


class TestSearchResult(unittest.TestCase):
    def test_strips_whitespace_and_is_json_serializable(self):
        r = SearchResult("  Title  ", " https://x.com ", "  snip  ")
        self.assertEqual(r, {"title": "Title", "url": "https://x.com", "snippet": "snip"})
        json.dumps(r)  # must not raise


class TestDuckDuckGoProvider(unittest.TestCase):
    def test_parses_results_and_unwraps_redirect_urls(self):
        with patch.object(DuckDuckGoProvider, "_fetch", return_value=DDG_HTML.encode()):
            results = DuckDuckGoProvider().search("test query", k=5)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["url"], "https://example.com/a")
        self.assertEqual(results[0]["title"], "First & Result")
        self.assertEqual(results[1]["url"], "https://example.com/b")

    def test_respects_k_limit(self):
        with patch.object(DuckDuckGoProvider, "_fetch", return_value=DDG_HTML.encode()):
            results = DuckDuckGoProvider().search("test query", k=1)
        self.assertEqual(len(results), 1)

    def test_no_results_is_empty_list_not_error(self):
        with patch.object(DuckDuckGoProvider, "_fetch", return_value=b"<html>no matches</html>"):
            self.assertEqual(DuckDuckGoProvider().search("nothing", k=5), [])


class TestBraveSearchProvider(unittest.TestCase):
    def test_parses_json_results(self):
        with patch.object(BraveSearchProvider, "_fetch", return_value=BRAVE_JSON.encode()):
            results = BraveSearchProvider("fake-key").search("test", k=5)
        self.assertEqual(results, [{"title": "Brave Result", "url": "https://brave.example/x", "snippet": "brave snippet"}])


class TestWebSearch(unittest.TestCase):
    def test_uses_duckduckgo_when_no_key_configured(self):
        ws = WebSearch(get_secret=lambda name, scope="global": None)
        status = ws.status()
        self.assertEqual(status["active_provider"], "duckduckgo")
        self.assertFalse(status["brave_key_set"])
        self.assertIsNotNone(status["hint"])

    def test_prefers_brave_when_key_configured(self):
        ws = WebSearch(get_secret=lambda name, scope="global": "k" if name == "BRAVE_API_KEY" else None)
        status = ws.status()
        self.assertEqual(status["active_provider"], "brave")
        self.assertTrue(status["brave_key_set"])
        self.assertIsNone(status["hint"])

    def test_falls_back_to_duckduckgo_when_brave_fails(self):
        ws = WebSearch(get_secret=lambda name, scope="global": "k" if name == "BRAVE_API_KEY" else None)
        with patch.object(BraveSearchProvider, "_fetch", side_effect=RuntimeError("brave down")), \
             patch.object(DuckDuckGoProvider, "_fetch", return_value=DDG_HTML.encode()):
            results = ws.search("test", k=5)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["url"], "https://example.com/a")

    def test_raises_when_every_provider_fails(self):
        ws = WebSearch(get_secret=lambda name, scope="global": None)
        with patch.object(DuckDuckGoProvider, "_fetch", side_effect=RuntimeError("network down")):
            with self.assertRaises(RuntimeError):
                ws.search("test", k=5)

    def test_key_added_after_construction_is_picked_up_immediately(self):
        secret = {}
        ws = WebSearch(get_secret=lambda name, scope="global": secret.get(name))
        self.assertEqual(ws.status()["active_provider"], "duckduckgo")
        secret["BRAVE_API_KEY"] = "late-key"  # e.g. a set_secret() call mid-session
        self.assertEqual(ws.status()["active_provider"], "brave")  # picked up without re-constructing WebSearch


if __name__ == "__main__":
    unittest.main()
