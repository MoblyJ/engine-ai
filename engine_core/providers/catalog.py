"""Model catalog — load, validate, and pin model IDs (upgrade.md §4).

Rules from the review:
- The catalog is FETCHED (OpenRouter's public /api/v1/models, no key needed) and cached
  under ~/.engine-ai/models.json — never hardcoded.
- Coding model IDs are PINNED explicitly after evaluation (`catalog:validated-*` refs
  resolve to whatever was pinned here). Popularity/context length never imply coding
  ability.
- `openrouter/free` is a SELECTOR, not a model: eligible(free_only) picks among free
  models and the selection is recorded per task — behavior is not assumed stable.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request

from engine_core.providers.base import ModelInfo

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
FREE_SELECTOR = "openrouter/free"


class CatalogError(ValueError):
    pass


def _model_from_api(entry: dict) -> ModelInfo:
    def num(x, default=0.0):
        try:
            return float(x)
        except (TypeError, ValueError):
            return default

    pricing = entry.get("pricing") or {}
    prompt = num(pricing.get("prompt"))
    completion = num(pricing.get("completion"))
    # OpenRouter marks ":free" routes with 0 pricing; negative values are cache discounts
    free = ":free" in str(entry.get("id", "")) or (prompt == 0.0 and completion == 0.0)
    arch = entry.get("architecture") or {}
    return ModelInfo(
        id=str(entry.get("id", "")),
        name=str(entry.get("name", "")),
        context_length=int(num((entry.get("context_length") or 0), 0)),
        max_output_tokens=int(num(entry.get("top_provider", {}).get("max_completion_tokens"), 0)),
        pricing_prompt=prompt, pricing_completion=completion,
        supports_tools=bool(arch.get("input_tool")),
        free=free,
        raw={"created": entry.get("created")},
    )


class Catalog:
    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(
            os.environ.get("ENGINE_AI_HOME", os.path.expanduser("~/.engine-ai")), "models.json")
        self.models: dict[str, ModelInfo] = {}
        self.pinned: dict[str, str] = {}
        self.refreshed_at: float = 0.0
        self._load()

    # ---------------------------------------------------------------- persistence
    def _load(self) -> bool:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.models = {}
            for m in data.get("models", []):
                info = ModelInfo(**m)
                self.models[info.id] = info
            self.pinned = dict(data.get("pinned", {}))
            self.refreshed_at = float(data.get("refreshed_at", 0))
            return True
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return False

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"refreshed_at": self.refreshed_at,
                       "models": [m.__dict__ for m in self.models.values()],
                       "pinned": self.pinned}, f)
        os.replace(tmp, self.path)

    # ---------------------------------------------------------------- fetch
    def refresh(self, url: str = OPENROUTER_MODELS_URL, timeout: int = 30) -> int:
        req = urllib.request.Request(url, headers={"User-Agent": "engine-ai/0.16"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        entries = data.get("data") or data if isinstance(data, dict) else data
        models = {}
        for entry in entries:
            try:
                info = _model_from_api(entry)
                if info.id:
                    models[info.id] = info
            except Exception:  # noqa: BLE001 - one bad entry never blocks the catalog
                continue
        if not models:
            raise CatalogError("catalog response contained no models")
        self.models = models
        self.refreshed_at = time.time()
        self.save()
        return len(models)

    def age_days(self) -> float:
        return (time.time() - self.refreshed_at) / 86400 if self.refreshed_at else float("inf")

    # ---------------------------------------------------------------- validate + pin
    def validate(self, model_id: str, *, needs_tools: bool = False, min_context: int = 8192) -> ModelInfo:
        """Validation gate: an ID is usable only if the catalog confirms it exists and
        meets the capability floor. Prices/context come from the catalog, not hope."""
        info = self.models.get(model_id)
        if not info:
            raise CatalogError(f"model {model_id!r} not in catalog (refresh first)")
        if info.context_length and info.context_length < min_context:
            raise CatalogError(f"model {model_id!r}: context {info.context_length} < {min_context}")
        if needs_tools and not info.supports_tools:
            raise CatalogError(f"model {model_id!r} does not support tool calling")
        return info

    def pin(self, name: str, model_id: str) -> None:
        self.validate(model_id)
        self.pinned[name] = model_id
        self.save()

    # ---------------------------------------------------------------- selection
    def eligible(self, *, free_only: bool = True, supports_tools: bool = False,
                 min_context: int = 8192) -> list[ModelInfo]:
        out = []
        for m in self.models.values():
            if free_only and not m.free:
                continue
            if supports_tools and not m.supports_tools:
                continue
            if m.context_length and m.context_length < min_context:
                continue
            out.append(m)
        return sorted(out, key=lambda m: (m.supports_tools, m.context_length), reverse=True)

    def resolve(self, ref: str) -> ModelInfo:
        """Resolve a profile's model_ref: `catalog:<name>` → pinned ID, `openrouter/free`
        → the currently eligible free pick (recorded, never assumed stable)."""
        if ref.startswith("catalog:"):
            name = ref.split(":", 1)[1]
            if name not in self.pinned:
                raise CatalogError(f"no pinned model named {name!r} — pin it after evaluation")
            return self.validate(self.pinned[name])
        if ref == FREE_SELECTOR:
            picks = self.eligible(free_only=True, supports_tools=True)
            if not picks:
                raise CatalogError("no eligible free models (refresh the catalog?)")
            return picks[0]
        return self.validate(ref)
