"""Provider interface — every gateway adapter implements this (upgrade.md §4)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModelInfo:
    id: str
    name: str = ""
    context_length: int = 0
    max_output_tokens: int = 0
    pricing_prompt: float = 0.0   # USD per 1M input tokens
    pricing_completion: float = 0.0
    supports_tools: bool = False
    free: bool = False
    raw: dict = field(default_factory=dict)

    def price(self, usage: "Usage") -> float:
        return (usage.input_tokens * self.pricing_prompt +
                usage.output_tokens * self.pricing_completion) / 1_000_000


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, other: "Usage") -> "Usage":
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.reasoning_tokens += other.reasoning_tokens
        self.cached_tokens += other.cached_tokens
        self.cost_usd += other.cost_usd
        return self

    def as_dict(self) -> dict:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "reasoning_tokens": self.reasoning_tokens, "cached_tokens": self.cached_tokens,
                "cost_usd": round(self.cost_usd, 6)}


@dataclass
class Message:
    role: str  # system | user | assistant | tool
    content: str = ""
    tool_call_id: str | None = None  # for tool results
    tool_calls: list[dict] = field(default_factory=list)  # assistant-requested calls
    name: str | None = None


@dataclass
class Completion:
    content: str
    model: str
    usage: Usage
    finish_reason: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


class ProviderError(Exception):
    """Typed provider failure — the worker decides retry/pause from `kind`, never guesses."""

    AUTH = "auth"                # 401/403 — stop, never rotate accounts
    QUOTA = "quota"              # 402 / out of credits — pause or stop, no endless retry
    RATE_LIMIT = "rate_limit"    # 429 — honor Retry-After, bounded jittered retry
    NETWORK = "network"          # timeouts, connection resets — retryable
    SERVER = "server"            # 5xx — retryable
    INVALID_OUTPUT = "invalid_output"  # schema-violating model response — no blind retry

    def __init__(self, kind: str, message: str, retry_after: float | None = None,
                 status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.retry_after = retry_after
        self.status = status

    @property
    def retryable(self) -> bool:
        return self.kind in (ProviderError.RATE_LIMIT, ProviderError.NETWORK, ProviderError.SERVER)


class Provider(ABC):
    name: str = "base"

    @abstractmethod
    def complete(self, model: str, messages: list[Message], *, tools: list[dict] | None = None,
                 max_output_tokens: int | None = None, temperature: float | None = None) -> Completion:
        """One model turn. Raises ProviderError with a typed kind on failure."""

    def close(self) -> None:  # optional teardown
        pass


def redact(obj: Any) -> Any:
    """Strip credential-shaped values before anything is journaled or logged."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if any(t in str(k).lower() for t in ("key", "token", "authorization", "credential")):
                out[k] = "***"
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj
