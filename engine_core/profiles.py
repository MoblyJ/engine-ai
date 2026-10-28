"""Per-agent profile assignments — API key + model per agent (upgrade.md §4).

Each agent runs under a PROFILE that carries its own credential reference and model.
A profile's key can be:

- `env:NAME`      — resolved from the environment at call time
- `vault:NAME`    — resolved from the encrypted engine-ai vault (`set_secret`)
- a literal key   — dev/test only; never put real keys in shared config files

Ten profiles (planner, frontend, backend, services, database, test-author, reviewer,
security-review, researcher, documentation) — the review's "ten profiles, not ten
accounts": separate keys give attribution/revocation/spending separation, NOT extra
quota, so the defaults share one account_group. Point a profile at its own account
group only when it genuinely belongs to a different OpenRouter account.

Agent mapping: engine agents are wired to profiles via DEFAULT_AGENTS (overridable in
the config's `agents:` section and in ~/.engine-ai/profiles.json); domain experts map
through experts.DOMAIN_PROFILE. Overrides persist locally and never contain key
material — only references.
"""

from __future__ import annotations

import json
import os

from engine_core.config import Config, ConfigError, Profile

DEFAULT_TEN = [
    "planner", "frontend", "backend", "services", "database",
    "test-author", "reviewer", "security-review", "researcher", "documentation",
]

# engine agent -> default profile (domain-<slug> experts map via experts.DOMAIN_PROFILE)
DEFAULT_AGENTS = {
    "engine-orchestrator": "planner",
    "engine-app-builder": "backend",
    "engine-mobile": "frontend",
    "engine-deployer": "services",
    "engine-researcher": "researcher",
    "engine-debugger": "test-author",
}


def _default_profile(name: str) -> Profile:
    env = "env:OPENROUTER_" + name.upper().replace("-", "_") + "_KEY"
    ref = "catalog:validated-" + name + "-model"
    return Profile(name=name, provider="openrouter", credential_ref=env,
                   account_group="team-primary", model_ref=ref,
                   input_target_tokens=12000, max_output_tokens=4000)


def default_profiles() -> dict[str, Profile]:
    return {n: _default_profile(n) for n in DEFAULT_TEN}


def vault_getter():
    """Lazy accessor for the encrypted vault (None when unavailable)."""
    try:
        from engine_core._compat import mcp_module
        return mcp_module("engine").Engine().get_secret
    except Exception:  # noqa: BLE001 - vault optional; env refs still work
        return None


def mask(key: str | None) -> str:
    if not key:
        return "MISSING"
    return f"set (…{key[-4:]})" if len(key) > 4 else "set"


class Assignments:
    """Config defaults ← config file ← local overrides (~/.engine-ai/profiles.json)."""

    def __init__(self, home: str | None = None, config: Config | None = None,
                 overrides_path: str | None = None):
        self.home = home or (os.environ.get("ENGINE_AI_HOME") or os.path.expanduser("~/.engine-ai"))
        self.overrides_path = overrides_path or os.path.join(self.home, "profiles.json")
        base = default_profiles()
        if config is not None:
            base.update(config.profiles)
        self.agents: dict[str, str] = dict(DEFAULT_AGENTS)
        if config is not None and getattr(config, "agent_profiles", None):
            self.agents.update(config.agent_profiles)
        self.overrides: dict = self._load()
        self.profiles: dict[str, Profile] = {}
        for name, p in base.items():
            ov = self.overrides.get("profiles", {}).get(name)
            if ov:
                p = Profile(name=name, provider=p.provider,
                            credential_ref=ov.get("credential_ref", p.credential_ref),
                            account_group=ov.get("account_group", p.account_group),
                            model_ref=ov.get("model_ref", p.model_ref),
                            input_target_tokens=int(ov.get("input_target_tokens", p.input_target_tokens)),
                            max_output_tokens=int(ov.get("max_output_tokens", p.max_output_tokens)),
                            requires=p.requires,
                            read_only=bool(ov.get("read_only", p.read_only)))
            self.profiles[name] = p
        self.agents.update(self.overrides.get("agents", {}))
        for agent, profile in self.agents.items():
            if profile not in self.profiles:
                raise ConfigError(f"agent {agent!r} maps to unknown profile {profile!r} "
                                  f"(have: {sorted(self.profiles)})")

    # ---------------------------------------------------------------- overrides
    def _load(self) -> dict:
        try:
            with open(self.overrides_path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:
        os.makedirs(self.home, exist_ok=True)
        tmp = self.overrides_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.overrides, f, indent=2)
        os.replace(tmp, self.overrides_path)

    def set(self, profile: str, credential_ref: str | None = None, model_ref: str | None = None,
            read_only: bool | None = None, account_group: str | None = None) -> Profile:
        if profile not in self.profiles:
            raise ConfigError(f"unknown profile {profile!r} (have: {sorted(self.profiles)})")
        entry = self.overrides.setdefault("profiles", {}).setdefault(profile, {})
        if credential_ref is not None:
            entry["credential_ref"] = credential_ref
        if model_ref is not None:
            entry["model_ref"] = model_ref
        if read_only is not None:
            entry["read_only"] = read_only
        if account_group is not None:
            entry["account_group"] = account_group
        self._save()
        # merge the just-saved override back so the returned profile is current
        base = self.profiles[profile]
        self.profiles[profile] = Profile(
            name=profile, provider=base.provider,
            credential_ref=entry.get("credential_ref", base.credential_ref),
            account_group=entry.get("account_group", base.account_group),
            model_ref=entry.get("model_ref", base.model_ref),
            input_target_tokens=base.input_target_tokens,
            max_output_tokens=base.max_output_tokens,
            requires=base.requires,
            read_only=bool(entry.get("read_only", base.read_only)))
        return self.profiles[profile]

    def clear(self, profile: str) -> bool:
        removed = self.overrides.get("profiles", {}).pop(profile, None) is not None
        if removed:
            self._save()
        return removed

    # ---------------------------------------------------------------- resolution
    def profile(self, name: str) -> Profile:
        if name not in self.profiles:
            raise ConfigError(f"no profile {name!r} (have: {sorted(self.profiles)})")
        return self.profiles[name]

    def profile_for_agent(self, agent: str) -> Profile:
        if agent not in self.agents:
            raise ConfigError(f"agent {agent!r} has no profile mapping "
                              f"(mapped: {sorted(self.agents)})")
        return self.profile(self.agents[agent])

    def resolve_key(self, profile: Profile | str, getter=None) -> str | None:
        p = self.profile(profile) if isinstance(profile, str) else profile
        return p.resolve_credential(getter or vault_getter())

    def status(self, catalog=None, getter=None) -> list[dict]:
        getter = getter or vault_getter()
        rows = []
        for name, p in sorted(self.profiles.items()):
            agents = sorted(a for a, prof in self.agents.items() if prof == name)
            row = {"profile": name, "agents": agents, "provider": p.provider,
                   "credential_ref": p.credential_ref or "(none)",
                   "key": mask(p.resolve_credential(getter)),
                   "model_ref": p.model_ref, "account_group": p.account_group,
                   "read_only": p.read_only}
            if catalog is not None:
                try:
                    row["model"] = catalog.resolve(p.model_ref).id
                except Exception as e:  # noqa: BLE001 - unresolvable refs shown, not fatal
                    row["model"] = f"✗ {e}"
            rows.append(row)
        return rows

    def domain_agent_profiles(self) -> dict[str, str]:
        """domain-<slug> expert -> worker profile (via the deterministic router)."""
        try:
            from engine_core._compat import mcp_module
            return {f"domain-{d}": p for d, p in mcp_module("experts").DOMAIN_PROFILE.items()}
        except Exception:  # noqa: BLE001
            return {}
