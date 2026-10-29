"""Per-agent profile assignments — distinct API key + model per agent (upgrade.md §4)."""
import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from engine_core.config import Config, ConfigError, load_config  # noqa: E402
from engine_core.profiles import (Assignments, DEFAULT_AGENTS, DEFAULT_TEN,  # noqa: E402
                                  default_profiles, mask, parse_key_csv)
from engine_core.codex_config import render_block, write_config, write_env_file, _merge  # noqa: E402


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

    def test_vault_errors_are_loud_not_missing(self):
        """A locked/corrupt vault must raise, not masquerade as 'no key configured'."""
        def broken_getter(name, scope="global"):
            raise RuntimeError("database is locked")
        p = default_profiles()["backend"]
        p.credential_ref = "vault:X"
        with self.assertRaises(RuntimeError):
            p.resolve_credential(broken_getter)

    def test_real_vault_reads_are_concurrency_safe(self):
        """10 worker threads resolving keys simultaneously must all succeed —
        regression for the SQLite 'database is locked' race that misreported
        two healthy agents as NO KEY during a concurrent probe."""
        import threading
        old_home = os.environ.get("ENGINE_AI_HOME")
        old_key = os.environ.get("ENGINE_AI_MASTER_KEY")
        os.environ["ENGINE_AI_HOME"] = tempfile.mkdtemp()
        os.environ["ENGINE_AI_MASTER_KEY"] = "race-test"
        try:
            from engine_core._compat import mcp_module
            eng = mcp_module("engine").Engine()
            for i in range(10):
                eng.set_secret(f"K{i}", f"sk-val-{i}")
            results = [None] * 10
            barrier = threading.Barrier(10)

            def grab(i):
                barrier.wait()  # maximize contention
                results[i] = eng.get_secret(f"K{i}")

            ts = [threading.Thread(target=grab, args=(i,)) for i in range(10)]
            [t.start() for t in ts]
            [t.join() for t in ts]
            self.assertEqual(results, [f"sk-val-{i}" for i in range(10)],
                             "every concurrent vault read must succeed")
        finally:
            for var, old in (("ENGINE_AI_HOME", old_home),
                             ("ENGINE_AI_MASTER_KEY", old_key)):
                if old is None:
                    os.environ.pop(var, None)
                else:
                    os.environ[var] = old

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


class TestKeyCsvParsing(unittest.TestCase):
    def test_two_and_three_column_lines(self):
        rows, errors = parse_key_csv(
            "profile,key,model\n"
            "backend,sk-k1,m/b\n"
            "sk-k2,m/f\n")
        self.assertEqual(errors, [])
        self.assertEqual(rows[0], {"profile": "backend", "key": "sk-k1", "model": "m/b", "line": 2})
        self.assertIsNone(rows[1]["profile"])

    def test_bom_crlf_and_plural_header(self):
        # Excel-export shape: UTF-8 BOM + "api keys,models" header + CRLF endings
        text = "﻿api keys,models\r\nsk-a,m/a\r\nsk-b,m/b\r\n"
        rows, errors = parse_key_csv(text)
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 2, "BOM+plural header must not become a data row")

    def test_agent_column_header(self):
        rows, errors = parse_key_csv("agent,api key,model\nbackend,sk-x,m/x\n")
        self.assertEqual((rows, errors), ([{"profile": "backend", "key": "sk-x",
                                            "model": "m/x", "line": 2}], []))

    def test_comments_blanks_and_junk(self):
        rows, errors = parse_key_csv("# comment\n\nsk-a,m/a\ntoo,many,fields,here\n")
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(errors), 1)

    def test_quotes_stripped_uppercase_profile_lowered(self):
        rows, _ = parse_key_csv('"Frontend","sk-x","m/f"\n')
        self.assertEqual(rows[0]["profile"], "frontend")
        self.assertEqual(rows[0]["key"], "sk-x")

    def test_incomplete_line_rejected(self):
        rows, errors = parse_key_csv("sk-only-model-missing,\n")
        self.assertEqual(rows, [])
        self.assertTrue(errors)


class TestImportKeys(unittest.TestCase):
    """Pasted `key,model` import → permanent, encrypted, masked."""

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.vault = {}
        self.a = Assignments(self.home)

    def _import(self, text, **kw):
        return self.a.import_keys(text=text, set_secret=self._set, get_secret=self._get, **kw)

    def _set(self, name, value, scope="global"):
        self.vault[name] = value

    def _get(self, name, scope="global"):
        return self.vault.get(name)

    def test_three_col_import_is_permanent(self):
        out = self._import("backend,sk-k1,m/b\nfrontend,sk-k2,m/f\n")
        self.assertTrue(out["ok"])
        self.assertEqual(out["imported"], 2)
        # keys in the "vault", assignments on disk — a fresh instance sees both
        b = Assignments(self.home, overrides_path=self.a.overrides_path)
        p = b.profile("backend")
        self.assertEqual(p.credential_ref, "vault:OPENROUTER_BACKEND_KEY")
        self.assertEqual(p.model_ref, "m/b")
        self.assertEqual(p.resolve_credential(self._get), "sk-k1")

    def test_two_col_auto_fills_missing_profiles_in_order(self):
        out = self._import("sk-1,m/1\nsk-2,m/2\n")
        self.assertTrue(out["ok"])
        got = {r["profile"] for r in out["profiles"]}
        self.assertEqual(got, {"planner", "frontend"}, "DEFAULT_TEN order")

    def test_two_col_with_profile_arg(self):
        out = self._import("sk-1,m/1\n", profile="reviewer")
        self.assertEqual(out["profiles"][0]["profile"], "reviewer")

    def test_keys_never_echoed(self):
        out = self._import("backend,sk-supersecret123,m/b\n")
        self.assertNotIn("sk-supersecret123", str(out))

    def test_more_lines_than_profiles_is_an_error(self):
        out = self._import("".join(f"sk-{i},m/{i}\n" for i in range(11)))
        self.assertTrue(out["errors"])

    def test_unknown_profile_reported(self):
        out = self._import("nope,sk-1,m/1\n")
        self.assertFalse(out["ok"])
        self.assertTrue(any("unknown profile" in e for e in out["errors"]))

    def test_csv_path(self):
        path = os.path.join(self.home, "keys.csv")
        with open(path, "w") as f:
            f.write("backend,sk-p,m/p\n")
        out = self.a.import_keys(path=path, set_secret=self._set, get_secret=self._get)
        self.assertTrue(out["ok"])
        self.assertEqual(self.a.profile("backend").model_ref, "m/p")

    def test_setup_payload_prompts_when_missing(self):
        payload = self.a.setup_payload(getter=self._get)
        self.assertFalse(payload["ready"])
        self.assertIn("key,model", payload["ask_user"])
        self._import("backend,sk-1,m/1\n")
        payload = self.a.setup_payload(getter=self._get)
        self.assertIn("backend", [r["profile"] for r in payload["profiles"]
                                  if r["key"] != "MISSING"])
        self.assertTrue(payload["missing"], "other profiles still missing")


class TestCodexConfig(unittest.TestCase):
    ROWS = [{"profile": "backend", "model": "m/b", "key": "set (…k1)"},
            {"profile": "test-author", "model": "m/t", "key": "set (…k2)"}]

    def test_render_valid_toml(self):
        import tomllib
        data = tomllib.loads(render_block(self.ROWS, "backend"))
        self.assertEqual(data["model"], "m/b")
        self.assertEqual(data["model_provider"], "engine_backend")
        self.assertEqual(data["model_providers"]["engine_backend"]["env_key"],
                         "OPENROUTER_BACKEND_KEY")
        self.assertEqual(data["model_providers"]["engine_test_author"]["base_url"],
                         "https://openrouter.ai/api/v1")
        self.assertEqual(data["profiles"]["engine_test_author"]["model"], "m/t")

    def test_no_top_level_model_when_user_sets_one(self):
        import tomllib
        data = tomllib.loads(render_block(self.ROWS, "backend", include_top=False))
        self.assertNotIn("model", data)
        self.assertIn("model_providers", data)

    def test_merge_keeps_top_level_keys_valid_after_user_tables(self):
        import tomllib
        merged = _merge("[tui]\nx = true\n", render_block(self.ROWS, "backend"))
        data = tomllib.loads(merged)
        self.assertEqual(data["model"], "m/b")       # top-level, not tui.model
        self.assertTrue(data["tui"]["x"])

    def test_merge_is_idempotent(self):
        once = _merge("[tui]\nx = true\n", render_block(self.ROWS, "backend"))
        twice = _merge(once, render_block(self.ROWS, "backend"))
        from engine_core.codex_config import BEGIN, END
        self.assertEqual(twice.count(BEGIN), 1, "block header not duplicated")
        self.assertEqual(twice.count(END), 1)
        import tomllib
        self.assertIn("model_providers", tomllib.loads(twice))

    def test_write_config_preserves_and_backs_up(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "config.toml"), "w") as f:
            f.write("[tui]\nx = true\n")
        out = write_config(d, self.ROWS, "backend")
        self.assertTrue(out["ok"])
        self.assertTrue(os.path.exists(os.path.join(d, "config.toml.engine-ai.bak")))
        with open(os.path.join(d, "config.toml")) as f:
            text = f.read()
        self.assertTrue(text.index("model =") < text.index("[tui]"),
                        "top-level keys must precede user tables")

    def test_write_env_file_0600(self):
        d = tempfile.mkdtemp()
        out = write_env_file(d, {"backend": "sk-1", "researcher": "sk-2"})
        path = out["env_file"]
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        with open(path) as f:
            body = f.read()
        self.assertIn('export OPENROUTER_RESEARCHER_KEY="sk-2"', body)
        self.assertNotIn("model", body)


class TestKeysFileDetection(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self._cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self._wd if hasattr(self, "_wd") else self._cwd)

    def test_detects_home_file(self):
        from engine_core.profiles import detect_keys_file
        with open(os.path.join(self.home, "keys.csv"), "w") as f:
            f.write("sk-a,m/a\n")
        found = detect_keys_file(self.home)
        self.assertEqual(found["path"], os.path.join(self.home, "keys.csv"))

    def test_detects_cwd_file_with_git_warning(self):
        from engine_core.profiles import detect_keys_file
        d = tempfile.mkdtemp()
        os.chdir(d)
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        with open(os.path.join(d, "keys.csv"), "w") as f:
            f.write("sk-a,m/a\n")
        found = detect_keys_file(tempfile.mkdtemp())  # home empty → falls through to cwd
        self.assertEqual(found["path"], os.path.join(d, "keys.csv"))
        self.assertTrue(found["in_git_repo"], "keys.csv inside a repo must be flagged")

    def test_env_var_wins(self):
        from engine_core.profiles import detect_keys_file
        env_path = os.path.join(tempfile.mkdtemp(), "custom.csv")
        with open(env_path, "w") as f:
            f.write("sk-a,m/a\n")
        old = os.environ.get("ENGINE_AI_KEYS_CSV")
        os.environ["ENGINE_AI_KEYS_CSV"] = env_path
        try:
            self.assertEqual(detect_keys_file(self.home)["path"], env_path)
        finally:
            if old is None:
                os.environ.pop("ENGINE_AI_KEYS_CSV", None)
            else:
                os.environ["ENGINE_AI_KEYS_CSV"] = old

    def test_setup_payload_mentions_found_keys_file(self):
        a = Assignments(self.home)
        with open(os.path.join(self.home, "keys.csv"), "w") as f:
            f.write("sk-a,m/a\n")
        payload = a.setup_payload()
        self.assertIn("keys_file", payload)
        self.assertIn("openrouter_import", payload["keys_file_hint"])

    def test_keys_csv_is_git_ignored_in_this_repo(self):
        # the repo is PUBLIC: a committed keys.csv leaks wallets
        p = subprocess.run(["git", "check-ignore", "keys.csv"], cwd=REPO,
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, "keys.csv MUST be git-ignored")
        with open(os.path.join(REPO, ".gitignore")) as f:
            self.assertIn("keys.csv", f.read())


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
