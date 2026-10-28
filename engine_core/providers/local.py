"""Local model adapter (upgrade.md §4) — OpenAI-compatible endpoints (Ollama, llama.cpp,
vLLM, LM Studio) for the RTX-class GPU box.

Capacity note from the review: check actual VRAM/latency for the specific model BEFORE
routing work here; a local model is not automatically cheaper or better. Start with
evaluated small-model tasks and embeddings only.
"""

from __future__ import annotations

from engine_core.providers.base import ProviderError
from engine_core.providers.limiter import Limits, RateLimiter
from engine_core.providers.openrouter import OpenRouterProvider


class LocalProvider(OpenRouterProvider):
    """Same wire protocol as OpenRouter (OpenAI-compatible /chat/completions), local base
    URL, no key, no rate limiter contention with cloud account groups."""

    name = "local"

    def __init__(self, base_url: str = "http://127.0.0.1:11434/v1", **kwargs):
        if kwargs.get("limiter") is None:
            rl = RateLimiter()  # local box: effectively unbounded, still journaled
            rl.configure("local", Limits(requests_per_minute=100_000, requests_per_day=10_000_000))
            kwargs["limiter"] = rl
        kwargs.setdefault("account_group", "local")
        super().__init__(api_key=kwargs.pop("api_key", "local"), base_url=base_url, **kwargs)

    def _post(self, path: str, body: dict) -> dict:
        try:
            return super()._post(path, body)
        except ProviderError as e:
            if e.kind == ProviderError.AUTH:  # local servers don't auth — downgrade to network
                raise ProviderError(ProviderError.NETWORK, f"local endpoint unreachable/auth: {e.message}") from e
            raise
