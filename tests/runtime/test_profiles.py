"""Per-agent profile assignments — distinct API key + model per agent (upgrade.md §4)."""
import os
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from engine_core.config import Config, ConfigError, load_config  # noqa: E402
from engine_core.profiles import (Assignments, DEFAULT_AGENTS, DEFAULT_TEN,  # noqa: E402
                                  default_profiles, mask)


class _FakeVault:
    """Dict-backed vault: only ever sees names, never leaks into config files."""
    def __init__(self, **keys):
        self.keys = keys

    def get(self, name, scope="global"):
        return self.keys.get(name)


class TestDefaults(unittest.TestCase):
    def test_ten_profiles_with_distinct_credential_refs(self):
        profiles = default_profiles()
        self.assertEqual(sorted(profiles), sorted(DEFAULT_TEN))
        refs = [p.credential_ref for p in profiles.values()]
        self.assertEqual(len(set(refs)), 10, "every profile gets its OWN credential ref")
        for p in profiles.values():
            self.assertTrue(p.credential_ref.startswith("env:OPENROUTER_"))
            self.assertTrue(p.model_ref.startswith("catalog:validated-"))

    def test_every_engine_agent_maps_to_a_profile(self):
        profiles = default_profiles()
        for agent, profile in DEFAULT_AGENTS.items():
            self.assertIn(profile, profiles, f"{agent} maps to unknown {profile}")

    def test_example_config_agents_section_parses(self):
        cfg = load_config(os.path.join(REPO, "config.example.yaml"))
        self.assertEqual(cfg.agent_profiles["engine-app-builder"], "backend")
        self.assertEqual(cfg.agent_profiles["engine-orchestrator"], "planner")
        self.assertEqual(len(cfg.profiles), 10)

    def test_config_rejects_agent_mapping_to_unknown_profile(self):
        with self.assertRaises(ConfigError):
            Config.from_dict({"profiles": {"a": {}}, "agents": {"x": "nope"}})


class TestCredentialResolution(unittest.TestCase):
    def test_env_ref(self):
        os.environ["ENGINE_T_KEY"] = "sk-env"
        try:
            p = default_profiles()["backend"]
            p.credential_ref = "env:ENGINE_T_KEY"
            self.assertEqual(p.resolve_credential(), "sk-env")
        finally:
            del os.environ["ENGINE_T_KEY"]

    def test_vault_ref(self):
        p = default_profiles()["backend"]
        p.credential_ref = "vault:MY_KEY"
        vault = _FakeVault(MY_KEY="sk-vault")
        self.assertEqual(p.resolve_credential(vault.get), "sk-vault",
                         "vault:NAME must resolve through the encrypted vault")
        self.assertIsNone(p.resolve_credential(), "no vault → treated as unset")
        self.assertIsNone(p.resolve_credential(_FakeVault().get), "vault miss → unset")

    def test_env_slicing_has_no_off_by_one(self):
        os.environ["K"] = "v"
        try:
            p = default_profiles()["backend"]
            p.credential_ref = "env:K"
            self.assertEqual(p.resolve_credential(), "v")
        finally:
            del os.environ["K"]

    def test_literal_ref_dev_only(self):
        p = default_profiles()["backend"]
        p.credential_ref = "sk-literal"
        self.assertEqual(p.resolve_credential(), "sk-literal")

    def test_mask(self):
        self.assertEqual(mask(None), "MISSING")
        self.assertEqual(mask("sk-abcd"), "set (…abcd)")
        self.assertNotIn("sk-abcd"[:-4], mask("sk-abcd"))


class TestAssignments(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.a = Assignments(self.home)

    def test_set_override_persists_and_merges(self):
        p = self.a.set("frontend", credential_ref="vault:FRONT", model_ref="v/m1",
                       read_only=True)
        self.assertEqual(p.credential_ref, "vault:FRONT")  # returned profile is current
        self.assertTrue(p.read_only)
        # a fresh instance sees the same override from disk
        b = Assignments(self.home)
        self.assertEqual(b.profile("frontend").credential_ref, "vault:FRONT")
        self.assertEqual(b.profile("frontend").model_ref, "v/m1")

    def test_clear_override(self):
        self.a.set("backend", model_ref="v/x")
        self.assertTrue(self.a.clear("backend"))
        self.assertFalse(self.a.clear("backend"))
        self.assertEqual(Assignments(self.home).profile("backend").model_ref,
                         "catalog:validated-backend-model")

    def test_override_file_never_contains_key_material(self):
        self.a.set("frontend", credential_ref="vault:FRONT")
        with open(os.path.join(self.home, "profiles.json")) as f:
            raw = f.read()
        self.assertNotIn("sk-", raw, "only references persist, never keys")

    def test_agent_profile_resolution(self):
        p = self.a.profile_for_agent("engine-app-builder")
        self.assertEqual(p.name, "backend")
        self.assertEqual(self.a.profile_for_agent("engine-mobile").name, "frontend")
        with self.assertRaises(ConfigError):
            self.a.profile_for_agent("unknown-agent")

    def test_status_masks_keys_and_marks_missing(self):
        vault = _FakeVault(OPENROUTER_FRONTEND_KEY="sk-or-front-9999")
        self.a.set("frontend", credential_ref="vault:OPENROUTER_FRONTEND_KEY")
        rows = {r["profile"]: r for r in self.a.status(getter=vault.get)}
        self.assertEqual(rows["frontend"]["key"], "set (…9999)")
        self.assertEqual(rows["backend"]["key"], "MISSING")
        self.assertIn("engine-app-builder", rows["backend"]["agents"])

    def test_domain_experts_map_to_worker_profiles(self):
        domain = self.a.domain_agent_profiles()
        self.assertGreater(len(domain), 20)
        self.assertEqual(domain["domain-frontend"], "frontend")
        for profile in domain.values():
            self.assertIn(profile, DEFAULT_TEN)


class TestRunKeyGuard(unittest.TestCase):
    """`run` refuses cleanly when the agent's profile has no key."""

    def setUp(self):
        import contextlib
        import io
        import engine_core.__main__ as cli
        self.cli = cli
        self.quiet_out = lambda: contextlib.redirect_stdout(io.StringIO())  # noqa: E731
        self.quiet_err = lambda: contextlib.redirect_stderr(io.StringIO())  # noqa: E731
        self.home = tempfile.mkdtemp()
        self._old_home, cli.HOME = cli.HOME, self.home
        self._old_env = os.environ.get("ENGINE_AI_HOME")
        os.environ["ENGINE_AI_HOME"] = self.home
        self.graph = os.path.join(self.home, "g.json")
        with open(self.graph, "w") as f:
            import json
            json.dump({"tasks": [{"id": "a"}]}, f)

    def tearDown(self):
        self.cli.HOME = self._old_home
        if self._old_env is None:
            os.environ.pop("ENGINE_AI_HOME", None)
        else:
            os.environ["ENGINE_AI_HOME"] = self._old_env

    def _run(self, argv):
        with self.quiet_out(), self.quiet_err():
            return self.cli.main(argv)

    def test_missing_key_is_a_clean_setup_error(self):
        code = self._run(["run", "--graph", self.graph, "--agent", "engine-app-builder"])
        self.assertEqual(code, 1)

    def test_env_key_passes_the_guard(self):
        # key present → guard passes, run proceeds to the (empty) catalog check
        os.environ["OPENROUTER_BACKEND_KEY"] = "sk-t"
        try:
            code = self._run(["run", "--graph", self.graph, "--profile", "backend"])
        finally:
            del os.environ["OPENROUTER_BACKEND_KEY"]
        self.assertEqual(code, 1)  # catalog error, not the key error

    def test_agent_flag_maps_to_its_profile(self):
        code = self._run(["run", "--graph", self.graph, "--agent", "engine-researcher"])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
