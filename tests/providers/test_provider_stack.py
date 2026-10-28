"""Provider stack: limiter reservation, catalog pinning, OpenRouter error mapping."""
import json
import os
import sys
import tempfile
import unittest
import urllib.error

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from engine_core.providers.base import Message, ModelInfo, ProviderError, Usage  # noqa: E402
from engine_core.providers.catalog import Catalog, CatalogError  # noqa: E402
from engine_core.providers.limiter import Limits, RateLimiter, backoff  # noqa: E402
from engine_core.providers.openrouter import OpenRouterProvider  # noqa: E402


class TestRateLimiter(unittest.TestCase):
    def test_reserves_within_limits(self):
        rl = RateLimiter()
        rl.configure("g", Limits(requests_per_minute=3, requests_per_day=10))
        for _ in range(3):
            ok, wait = rl.reserve("g")
            self.assertTrue(ok)
        ok, wait = rl.reserve("g")
        self.assertFalse(ok, "4th call in a minute must be rejected")

    def test_daily_quota_blocks_with_long_wait(self):
        rl = RateLimiter()
        rl.configure("g", Limits(requests_per_minute=100, requests_per_day=2))
        rl.reserve("g", 2)
        ok, wait = rl.reserve("g")
        self.assertFalse(ok)
        self.assertGreater(wait, 3600, "daily exhaustion must signal a PAUSE, not a short retry")

    def test_reservation_is_atomic_under_threads(self):
        import threading
        rl = RateLimiter()
        rl.configure("g", Limits(requests_per_minute=10, requests_per_day=100))
        wins = []
        barrier = threading.Barrier(12)

        def grab():
            barrier.wait()
            ok, _ = rl.reserve("g")
            wins.append(ok)

        ts = [threading.Thread(target=grab) for _ in range(12)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(sum(wins), 10, "12 contenders, 10 slots — never more winners than slots")

    def test_pause_honored(self):
        rl = RateLimiter()
        rl.pause("g", 30)
        ok, wait = rl.reserve("g")
        self.assertFalse(ok)
        self.assertGreater(wait, 0)

    def test_backoff_bounded_with_jitter(self):
        for attempt in (1, 2, 5, 20):
            d = backoff(attempt, base=1, cap=10)
            self.assertLessEqual(d, 10 + 1e-9)
            self.assertGreaterEqual(d, 0)


def _transport(status, payload):
    def t(url, body, headers, timeout):
        if status == "network":
            raise urllib.error.URLError("boom")
        return status, payload
    return t


class TestOpenRouterMapping(unittest.TestCase):
    def _provider(self, status, payload):
        rl = RateLimiter()
        rl.configure("g", Limits(requests_per_minute=1000, requests_per_day=10_000))
        return OpenRouterProvider(api_key="k", limiter=rl, account_group="g",
                                  transport=_transport(status, payload))

    def test_success_completion_with_usage(self):
        p = self._provider(200, {"choices": [{"message": {"content": "hi"},
                                               "finish_reason": "stop"}],
                                 "model": "m", "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                                 "prompt_tokens_details": {"cached_tokens": 4}, "cost": 0.001}})
        c = p.complete("m", [Message("user", "x")])
        self.assertEqual(c.content, "hi")
        self.assertEqual(c.usage.input_tokens, 10)
        self.assertEqual(c.usage.cached_tokens, 4)

    def test_auth_error_is_never_retryable(self):
        p = self._provider(401, {"error": {"message": "bad key"}})
        with self.assertRaises(ProviderError) as cm:
            p.complete("m", [])
        self.assertEqual(cm.exception.kind, ProviderError.AUTH)
        self.assertFalse(cm.exception.retryable)
        self.assertNotIn("k=", cm.exception.message)  # the key never appears in errors

    def test_rate_limit_carries_retry_after_and_pauses_group(self):
        p = self._provider(429, {"error": {"message": "slow down", "retry-after": 12}})
        with self.assertRaises(ProviderError) as cm:
            p.complete("m", [])
        self.assertEqual(cm.exception.kind, ProviderError.RATE_LIMIT)
        self.assertEqual(cm.exception.retry_after, 12)
        ok, wait = p.limiter.reserve("g")  # 429 pauses the WHOLE account group
        self.assertFalse(ok)
        self.assertGreaterEqual(wait, 10)

    def test_quota_402_maps_to_quota(self):
        p = self._provider(402, {"error": {"message": "no credits"}})
        with self.assertRaises(ProviderError) as cm:
            p.complete("m", [])
        self.assertEqual(cm.exception.kind, ProviderError.QUOTA)

    def test_network_failure_retries_then_raises(self):
        p = self._provider("network", None)
        with self.assertRaises(ProviderError) as cm:
            p._post("/chat/completions", {"model": "m"})
        self.assertEqual(cm.exception.kind, ProviderError.NETWORK)


class TestCatalog(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "models.json")
        self.cat = Catalog(path=self.path)
        self.cat.models = {
            "a/free": ModelInfo(id="a/free", free=True, supports_tools=True, context_length=32000),
            "b/paid": ModelInfo(id="b/paid", free=False, supports_tools=True, context_length=128000,
                                pricing_prompt=3.0, pricing_completion=15.0),
            "c/notools": ModelInfo(id="c/notools", free=True, supports_tools=False, context_length=8000),
        }
        self.cat.save()

    def test_roundtrip_and_pin(self):
        cat2 = Catalog(path=self.path)
        self.assertIn("a/free", cat2.models)
        cat2.pin("validated-backend-model", "b/paid")
        cat3 = Catalog(path=self.path)
        self.assertEqual(cat3.resolve("catalog:validated-backend-model").id, "b/paid")

    def test_validate_rejects_missing_capabilities(self):
        with self.assertRaises(CatalogError):
            self.cat.validate("c/notools", needs_tools=True)
        with self.assertRaises(CatalogError):
            self.cat.validate("c/notools", min_context=16000)
        with self.assertRaises(CatalogError):
            self.cat.validate("ghost/model")

    def test_free_selector_picks_eligible_and_recorded(self):
        pick = self.cat.resolve("openrouter/free")
        self.assertTrue(pick.free)
        self.assertTrue(pick.supports_tools)

    def test_refresh_from_fake_api(self):
        def fake_urlopen(req, timeout):
            class R:
                def read(self):
                    return json.dumps({"data": [
                        {"id": "x/y:free", "name": "Y", "context_length": 64000,
                         "top_provider": {"max_completion_tokens": 4096},
                         "pricing": {"prompt": "0", "completion": "0"},
                         "architecture": {"input_tool": True}}]}).encode()
                def __enter__(self): return self
                def __exit__(self, *a): return False
            return R()
        import engine_core.providers.catalog as cat
        old = cat.urllib.request.urlopen
        cat.urllib.request.urlopen = fake_urlopen
        try:
            n = self.cat.refresh(url="https://example.invalid/models")
            self.assertEqual(n, 1)
            self.assertTrue(self.cat.models["x/y:free"].free)
            self.assertGreater(self.cat.refreshed_at, 0)
        finally:
            cat.urllib.request.urlopen = old

    def test_usage_pricing(self):
        m = self.cat.models["b/paid"]
        cost = m.price(Usage(input_tokens=1_000_000, output_tokens=1_000_000))
        self.assertAlmostEqual(cost, 18.0)


if __name__ == "__main__":
    unittest.main()
