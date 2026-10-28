"""Runtime configuration — YAML subset or JSON, validated (upgrade.md §4).

The illustrative config from the review is supported verbatim:

    version: 1
    execution:
      max_active_workers: 2
      max_build_processes: 1
      max_model_turns_per_task: 8
    routing:
      free_only: true
      paid_fallback: false
      quota_exhausted: pause
    profiles:
      frontend:
        provider: openrouter
        credential_ref: env:OPENROUTER_FRONTEND_KEY
        account_group: team-primary
        model_ref: catalog:validated-frontend-model
        input_target_tokens: 12000
        max_output_tokens: 4000
        requires: [tools]

Credential refs are RESOLVED ONLY by the provider adapter at call time (env lookup) —
they are never resolved into prompts, shared config dumps, or telemetry.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

QUOTA_ACTIONS = ("pause", "stop", "fail")
KNOWN_PROVIDERS = ("openrouter", "local")


class ConfigError(ValueError):
    pass


# --------------------------------------------------------- tiny YAML-subset parser
def _parse_scalar(s: str):
    s = s.strip()
    if s.startswith("[") and s.endswith("]"):  # inline list: [a, b, c]
        inner = s[1:-1].strip()
        return [_parse_scalar(x) for x in inner.split(",")] if inner else []
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    low = s.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~", ""):
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _parse_block(lines: list[tuple[int, str]], i: int, indent: int):
    """Parse the block starting at lines[i] whose lines sit at exactly `indent`.
    Returns (value, index of the first line AFTER this block)."""
    if i < len(lines) and lines[i][1].startswith("- "):
        items = []
        while i < len(lines) and lines[i][0] == indent and lines[i][1].startswith("- "):
            items.append(_parse_scalar(lines[i][1][2:]))
            i += 1
        return items, i
    mapping: dict = {}
    while i < len(lines):
        ind, line = lines[i]
        if ind < indent:
            break
        if ind > indent:
            raise ConfigError(f"unexpected indent: {line!r}")
        if line.startswith("- ") or ":" not in line:
            raise ConfigError(f"expected 'key: value' or 'key:': {line!r}")
        key, _, rest = line.partition(":")
        key, rest = key.strip(), rest.strip()
        if rest:
            mapping[key] = _parse_scalar(rest)
            i += 1
        elif i + 1 < len(lines) and lines[i + 1][0] > indent:
            mapping[key], i = _parse_block(lines, i + 1, lines[i + 1][0])
        else:
            mapping[key] = None
            i += 1
    return mapping, i


def parse_yaml(text: str) -> dict:
    """Parse the YAML subset used by engine configs: nested maps, `- ` block lists,
    `[a, b]` inline lists, scalars, `#` comments. Not a general YAML parser."""
    lines = [(len(r) - len(r.lstrip(" ")), r.strip()) for r in text.splitlines()
             if r.strip() and not r.lstrip().startswith("#")]
    if not lines:
        return {}
    value, _ = _parse_block(lines, 0, lines[0][0])
    if not isinstance(value, dict):
        raise ConfigError("config root must be a mapping")
    return value


def load_config(path: str) -> "Config":
    with open(path, encoding="utf-8") as f:
        text = f.read()
    data = json.loads(text) if path.endswith(".json") else parse_yaml(text)
    return Config.from_dict(data)


# --------------------------------------------------------- typed config
@dataclass
class Profile:
    name: str
    provider: str = "openrouter"
    credential_ref: str | None = None
    account_group: str = "default"
    model_ref: str = "openrouter/free"
    input_target_tokens: int = 12000
    max_output_tokens: int = 4000
    requires: list = field(default_factory=list)
    read_only: bool = False

    def resolve_credential(self, vault_getter=None) -> str | None:
        """Resolve a credential ref — the ONLY place keys are looked up.

        env:NAME → environment; vault:NAME → the encrypted engine-ai vault
        (set_secret); anything else is treated as a literal key (dev/tests only).
        The resolved value never appears in prompts, config dumps, or telemetry.
        """
        ref = self.credential_ref
        if not ref:
            return None
        if ref.startswith("env:"):
            return os.environ.get(ref[len("env:"):])
        if ref.startswith("vault:"):
            if vault_getter is None:
                return None
            try:
                return vault_getter(ref[len("vault:"):])
            except Exception:  # noqa: BLE001 - vault miss/locked → treated as unset
                return None
        return ref


@dataclass
class Config:
    version: int = 1
    max_active_workers: int = 2
    max_build_processes: int = 1
    max_model_turns_per_task: int = 8
    free_only: bool = True
    paid_fallback: bool = False
    quota_exhausted: str = "pause"
    profiles: dict = field(default_factory=lambda: {"backend": Profile("backend")})
    agent_profiles: dict = field(default_factory=dict)  # agent name -> profile name

    @staticmethod
    def from_dict(data: dict) -> "Config":
        if not isinstance(data, dict):
            raise ConfigError("config root must be a mapping")
        if int(data.get("version", 1)) != 1:
            raise ConfigError(f"unsupported config version {data.get('version')!r}")
        ex = data.get("execution") or {}
        rt = data.get("routing") or {}
        cfg = Config(
            max_active_workers=int(ex.get("max_active_workers", 2)),
            max_build_processes=int(ex.get("max_build_processes", 1)),
            max_model_turns_per_task=int(ex.get("max_model_turns_per_task", 8)),
            free_only=bool(rt.get("free_only", True)),
            paid_fallback=bool(rt.get("paid_fallback", False)),
            quota_exhausted=str(rt.get("quota_exhausted", "pause")),
        )
        if cfg.quota_exhausted not in QUOTA_ACTIONS:
            raise ConfigError(f"routing.quota_exhausted must be one of {QUOTA_ACTIONS}")
        if cfg.paid_fallback and cfg.free_only:
            raise ConfigError("paid_fallback=true requires free_only=false (default is free-only)")
        if cfg.max_active_workers < 1 or cfg.max_model_turns_per_task < 1:
            raise ConfigError("max_active_workers / max_model_turns_per_task must be >= 1")
        profiles = {}
        for name, spec in (data.get("profiles") or {"backend": {}}).items():
            spec = spec or {}
            p = Profile(name=str(name),
                        provider=str(spec.get("provider", "openrouter")),
                        credential_ref=spec.get("credential_ref"),
                        account_group=str(spec.get("account_group", "default")),
                        model_ref=str(spec.get("model_ref", "openrouter/free")),
                        input_target_tokens=int(spec.get("input_target_tokens", 12000)),
                        max_output_tokens=int(spec.get("max_output_tokens", 4000)),
                        requires=list(spec.get("requires") or []),
                        read_only=bool(spec.get("read_only", False)))
            if p.provider not in KNOWN_PROVIDERS:
                raise ConfigError(f"profile {name!r}: unknown provider {p.provider!r}")
            if p.input_target_tokens < 500:
                raise ConfigError(f"profile {name!r}: input_target_tokens too small (<500)")
            profiles[name] = p
        if not profiles:
            raise ConfigError("at least one profile is required")
        cfg.profiles = profiles
        agents = data.get("agents") or {}
        if not isinstance(agents, dict):
            raise ConfigError("agents must be a mapping of agent name -> profile name")
        for agent, profile_name in agents.items():
            if profile_name not in profiles:
                raise ConfigError(f"agent {agent!r} maps to unknown profile {profile_name!r}")
        cfg.agent_profiles = dict(agents)
        return cfg

    def profile(self, name: str) -> Profile:
        if name not in self.profiles:
            raise ConfigError(f"no profile {name!r} (have: {sorted(self.profiles)})")
        return self.profiles[name]
