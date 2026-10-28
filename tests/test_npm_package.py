"""npm package hygiene — the tarball must ship what the CLI needs, and no bytecode."""
import json
import os
import re
import subprocess
import tarfile
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _tarball_files() -> list[str]:
    """Authoritative listing: pack for real (in a temp dir) and list the tarball."""
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["npm", "pack", "--silent", "--pack-destination", d],
                       cwd=REPO, capture_output=True, text=True, timeout=180, check=True)
        tgz = [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".tgz")][0]
        with tarfile.open(tgz) as tar:
            return tar.getnames()


class TestNpmPackage(unittest.TestCase):
    def test_no_bytecode_or_caches_in_tarball(self):
        files = _tarball_files()
        self.assertTrue(files, "npm pack produced an empty tarball")
        dirty = [f for f in files if "__pycache__" in f or f.endswith((".pyc", ".pyo"))]
        self.assertEqual(dirty, [], f"bytecode leaked into the npm tarball: {dirty[:5]}")

    def test_tarball_ships_the_runtime(self):
        files = "\n".join(_tarball_files())
        for required in ("package/mcp/forge_mcp.py", "package/engine_core/__main__.py",
                         "package/engine_core/profiles.py", "package/engine_core/codex_config.py",
                         "package/install.sh", "package/config.example.yaml",
                         "package/bin/engine-ai.js"):
            self.assertIn(required, files, f"{required} missing from the npm tarball")

    def test_versions_agree(self):
        with open(os.path.join(REPO, "package.json")) as f:
            pkg = json.load(f)["version"]
        with open(os.path.join(REPO, "engine_core", "__init__.py")) as f:
            core = re.search(r'__version__ = "([^"]+)"', f.read()).group(1)
        self.assertEqual(pkg, core, "package.json and engine_core versions must match")


if __name__ == "__main__":
    unittest.main()
