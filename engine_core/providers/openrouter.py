"""OpenRouter gateway adapter (upgrade.md §4).

- Credentials resolve from `credential_ref` (env:NAME) at call time and appear in NO
  error message, log, or telemetry (errors carry the masked ref at most).
- 429 → ProviderError(RATE_LIMIT) carrying Retry-After; 401/403 → AUTH (stop, never
  rotate); 402 → QUOTA (pause); 5xx/timeouts → retryable.
- Every request goes through the per-account-group RateLimiter; the limiter is paused
  on Retry-After so ALL workers in the group back off, not just the caller.
- Usage (incl. cached/reasoning tokens when reported) is attached to every completion.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from engine_core.providers.base import (Completion, Message, Provider, ProviderError, Usage)
from engine_core.providers.limiter import RateLimiter, backoff

API_BASE = "https://openrouter.ai/api/v1"


def _mask(ref: str | None) -> str:
    if not ref:
        return "(unset)"
    return ref if ref.startswith("env:") else "***"


class OpenRouterProvider(Provider):
    name = "openrouter"

    def __init__(self, api_key: str | None = None, credential_ref: str | None = None,
                 base_url: str = API_BASE, limiter: RateLimiter | None = None,
                 account_group: str = "default", timeout: int = 120,
                 max_retries: int = 3, transport=None):
        self._api_key = api_key
        self.credential_ref = credential_ref
        self.base_url = base_url.rstrip("/")
        self.limiter = limiter or RateLimiter()
        self.account_group = account_group
        self.timeout = timeout
        self.max_retries = max_retries
        # transport(url, body_dict, headers, timeout) -> (status, body_dict) — injectable
        # for tests; defaults to urllib so production has no extra deps.
        self._transport = transport or self._urllib_transport

    def _key(self) -> str:
        if self._api_key:
            return self._api_key
        if self.credential_ref and self.credential_ref.startswith("env:"):
            import os
            return os.environ.get(self.credential_ref[4:], "")
        return ""

    @staticmethod
    def _urllib_transport(url: str, body: dict, headers: dict, timeout: int):
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read().decode("utf-8"))
            except Exception:  # noqa: BLE001
                payload = {"error": {"message": str(e)}}
            return e.code, payload
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            raise ProviderError(ProviderError.NETWORK, f"transport failure: {e.__class__.__name__}") from e

    # ---------------------------------------------------------------- request
    def _post(self, path: str, body: dict) -> dict:
        url = self.base_url + path
        headers = {"Content-Type": "application/json", "HTTP-Referer": "https://github.com/MoblyJ/engine-ai",
                   "X-Title": "engine-ai", "Authorization": f"Bearer {self._key()}"}
        body = dict(body)
        body.setdefault("usage", {"include": True})

        last_error: ProviderError | None = None
        for attempt in range(1, self.max_retries + 1):
            ok, wait = self.limiter.reserve(self.account_group)
            if not ok:
                if wait > 3600:  # daily quota — pause, do not spin
                    raise ProviderError(ProviderError.QUOTA,
                                        f"daily quota for group {self.account_group!r} exhausted; "
                                        f"resume after ~{wait / 3600:.1f}h")
                time.sleep(min(wait, 30.0))
                continue
            try:
                status, payload = self._transport(url, body, headers, self.timeout)
            except ProviderError as e:
                last_error = e  # network/server: retry below when allowed
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_error = ProviderError(ProviderError.NETWORK,
                                           f"transport failure: {e.__class__.__name__}")
            else:
                if status == 200:
                    return payload
                err = payload.get("error") or {}
                msg = str(err.get("message") or payload)[:500]
                if status == 429:
                    retry_after = self._retry_after(err, payload)
                    self.limiter.pause(self.account_group, retry_after or backoff(attempt))
                    last_error = ProviderError(ProviderError.RATE_LIMIT, msg, retry_after=retry_after, status=429)
                elif status in (401, 403):
                    raise ProviderError(ProviderError.AUTH,
                                        f"auth failed (credential {_mask(self.credential_ref)}): {msg}", status=status)
                elif status == 402:
                    raise ProviderError(ProviderError.QUOTA, f"out of credits: {msg}", status=402)
                elif 500 <= status < 600:
                    last_error = ProviderError(ProviderError.SERVER, msg, status=status)
                else:
                    raise ProviderError(ProviderError.INVALID_OUTPUT,
                                        f"unexpected status {status}: {msg}", status=status)
            if last_error is not None and last_error.retryable and attempt < self.max_retries:
                time.sleep(backoff(attempt))
                continue
            if last_error is not None:
                raise last_error
        raise last_error or ProviderError(ProviderError.SERVER, "request failed")

    @staticmethod
    def _retry_after(err: dict, payload: dict) -> float | None:
        for source in (err, payload):
            for key in ("retry-after", "retry_after", "Retry-After"):
                v = source.get(key) if isinstance(source, dict) else None
                if v:
                    try:
                        return max(0.0, float(v))
                    except (TypeError, ValueError):
                        continue
        return None

    # ---------------------------------------------------------------- completion
    @staticmethod
    def _wire_messages(messages: list[Message]) -> list[dict]:
        """OpenAI chat wire format: assistant turns carry tool_calls; tool results
        carry tool_call_id (missing either is a 400 from OpenRouter/OpenAI)."""
        out = []
        for m in messages:
            if m.role == "assistant" and m.tool_calls:
                out.append({"role": "assistant", "content": m.content or None,
                            "tool_calls": m.tool_calls})
            elif m.role == "tool":
                out.append({"role": "tool", "tool_call_id": m.tool_call_id,
                            "content": m.content or "(empty result)"})
            else:
                out.append({"role": m.role, "content": m.content})
        return out

    def complete(self, model: str, messages: list[Message], *, tools: list[dict] | None = None,
                 max_output_tokens: int | None = None, temperature: float | None = None) -> Completion:
        body: dict = {"model": model, "messages": self._wire_messages(messages)}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if max_output_tokens:
            body["max_tokens"] = int(max_output_tokens)
        if temperature is not None:
            body["temperature"] = float(temperature)
        payload = self._post("/chat/completions", body)
        choices = payload.get("choices") or [{}]
        choice = choices[0] or {}
        msg = choice.get("message") or {}
        usage_raw = payload.get("usage") or {}
        usage = Usage(
            input_tokens=int(usage_raw.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage_raw.get("completion_tokens", 0) or 0),
            reasoning_tokens=int((usage_raw.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0),
            cached_tokens=int((usage_raw.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0),
            cost_usd=float(usage_raw.get("cost", 0) or 0),
        )
        return Completion(
            content=str(msg.get("content") or ""),
            model=str(payload.get("model", model)),
            usage=usage,
            finish_reason=str(choice.get("finish_reason") or ""),
            tool_calls=list(msg.get("tool_calls") or []),
            raw={"id": payload.get("id")},
        )
