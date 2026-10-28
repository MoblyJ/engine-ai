"""Deploy-readiness scanner + deployable-app scaffolder + verification runner.

`deploy_readiness(path)` inspects a project and separates:
- **inventory** — what files/config exist (Dockerfile, CI, env template, README, …)
- **evidence** — what the project's own manifests actually declare (parsed package.json
  scripts, detected test framework, CI workflow names, exact git commit)

Inventory alone never claims the app works. `verify(path)` goes further and EXECUTES the
detected test/build commands, returning an evidence manifest bound to the commit.

`scaffold(path, kind)` writes a minimal but genuinely deployable skeleton. It refuses to
overwrite existing files unless `replace=True` (originals are kept as `*.engine-ai.bak`),
and `dry_run=True` previews without writing anything.
"""

from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import time

SKIP_DIRS = {".git", "node_modules", "target", "dist", "build", ".venv", "__pycache__"}

# anchored patterns only — a file named `latest.txt` is NOT a test
TEST_PATTERNS = {
    "python": ["test_*.py", "*_test.py"],
    "node": ["*.test.js", "*.spec.js", "*.test.ts", "*.spec.ts", "*.test.tsx", "*.spec.tsx",
             "*.test.jsx", "*.spec.jsx", "*.test.mjs", "*.spec.mjs", "*.test.cjs", "*.spec.cjs"],
    "go": ["*_test.go"],
    "rust": [],  # rust convention: a top-level tests/ dir
}
TEST_CONFIGS = ("pytest.ini", "tox.ini", "jest.config.js", "jest.config.ts", "jest.config.mjs",
                "jest.config.cjs", "vitest.config.ts", "vitest.config.js", "playwright.config.ts",
                "karma.conf.js", ".mocharc.yml", ".mocharc.json")
TEST_DIRS = ("__tests__", "tests", "test")


def _exists(root, *names):
    return any(os.path.exists(os.path.join(root, n)) for n in names)


def detect_stack(root: str) -> str:
    if _exists(root, "package.json"):
        return "node"
    if _exists(root, "pyproject.toml", "requirements.txt", "setup.py"):
        return "python"
    if _exists(root, "go.mod"):
        return "go"
    if _exists(root, "Cargo.toml"):
        return "rust"
    return "unknown"


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def node_scripts(root: str) -> dict:
    pkg = _read_json(os.path.join(root, "package.json"))
    scripts = (pkg or {}).get("scripts") or {}
    return scripts if isinstance(scripts, dict) else {}


def _has_ci(root):
    wf = os.path.join(root, ".github", "workflows")
    if os.path.isdir(wf) and any(os.listdir(wf)):
        return True
    return _exists(root, ".gitlab-ci.yml")


def _ci_workflows(root: str) -> list[str]:
    wf = os.path.join(root, ".github", "workflows")
    if os.path.isdir(wf):
        return sorted(f for f in os.listdir(wf) if f.endswith((".yml", ".yaml")))
    return [".gitlab-ci.yml"] if _exists(root, ".gitlab-ci.yml") else []


def _walk_files(root: str):
    for dp, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            yield dp, f


def _has_tests(root: str, stack: str, scripts: dict) -> bool:
    if stack == "node" and str(scripts.get("test") or "").strip():
        return True  # a declared test script is configured evidence
    if _exists(root, *TEST_CONFIGS):
        return True
    patterns = TEST_PATTERNS.get(stack)
    if patterns is None:  # unknown stack: try every pattern family
        patterns = [p for ps in TEST_PATTERNS.values() for p in ps]
    for dp, f in _walk_files(root):
        if any(fnmatch.fnmatch(f, p) for p in patterns):
            return True
        if os.path.basename(dp) in TEST_DIRS and f.endswith((".py", ".js", ".ts", ".rs")):
            return True
    return False


def detect_test_framework(root: str, stack: str, scripts: dict) -> str | None:
    """Name the test framework the project itself declares/uses (None if none found)."""
    if _exists(root, "pytest.ini") or _exists(root, "tox.ini"):
        return "pytest"
    pyproject = _read_json(os.path.join(root, "pyproject.toml"))
    if isinstance(pyproject, dict) and "pytest" in (pyproject.get("tool") or {}):
        return "pytest"
    if stack == "node":
        cmd = str(scripts.get("test") or "")
        for fw in ("vitest", "jest", "mocha", "playwright", "node --test"):
            if fw in cmd:
                return fw
        for f in ("vitest.config.ts", "vitest.config.js"):
            if _exists(root, f):
                return "vitest"
        for f in ("jest.config.js", "jest.config.ts", "jest.config.mjs", "jest.config.cjs"):
            if _exists(root, f):
                return "jest"
        if cmd:
            return "npm test"
    if stack == "go":
        return "go test"
    if stack == "rust":
        return "cargo test"
    if stack == "python":
        for dp, f in _walk_files(root):
            if fnmatch.fnmatch(f, "test_*.py") or fnmatch.fnmatch(f, "*_test.py"):
                return "pytest" if _exists(root, "pytest.ini", "tox.ini") else "unittest"
    return None


def _git(root: str, *args: str) -> str | None:
    try:
        p = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=20,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def gather_evidence(root: str) -> dict:
    """What the project's own manifests declare — no claims about behavior."""
    stack = detect_stack(root)
    scripts = node_scripts(root) if stack == "node" else {}
    return {
        "commit": _git(root, "rev-parse", "HEAD"),
        "branch": _git(root, "rev-parse", "--abbrev-ref", "HEAD"),
        "scripts": {k: v for k, v in scripts.items() if k in ("start", "test", "build", "lint")},
        "test_framework": detect_test_framework(root, stack, scripts),
        "ci_workflows": _ci_workflows(root),
        "basis": "inventory+manifests (no commands executed)",
    }


def _test_commands(root: str, stack: str, scripts: dict, framework: str | None) -> list[tuple[str, list[str]]]:
    """Concrete test commands derived from the project's own declarations."""
    if stack == "node" and str(scripts.get("test") or "").strip():
        return [("npm test", ["npm", "test", "--silent"])]
    if framework == "pytest":
        return [("pytest", ["python3", "-m", "pytest", "-q"])]
    if framework == "unittest":
        return [("unittest", ["python3", "-m", "unittest", "discover", "-q"])]
    if framework == "go test":
        return [("go test", ["go", "test", "./..."])]
    if framework == "cargo test":
        return [("cargo test", ["cargo", "test"])]
    return []


def verify(root: str, timeout: int = 300) -> dict:
    """Execute the project's own test command(s); return an evidence manifest.

    Commands come only from parsed manifests above (npm test / pytest / unittest /
    go test / cargo test) — never from free-form shell strings in arbitrary files.
    """
    stack = detect_stack(root)
    scripts = node_scripts(root) if stack == "node" else {}
    framework = detect_test_framework(root, stack, scripts)
    cmds = _test_commands(root, stack, scripts, framework)
    results = []
    for name, argv in cmds:
        t0 = time.time()
        try:
            p = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=timeout,
                               stdin=subprocess.DEVNULL)
            code, tail = p.returncode, (p.stdout + p.stderr)[-2000:]
        except subprocess.TimeoutExpired:
            code, tail = None, f"timed out after {timeout}s"
        except OSError as e:  # not installed / not executable — a failed check, not a crash
            code, tail = None, f"{argv[0]}: cannot execute ({e.__class__.__name__})"
        results.append({"name": name, "command": " ".join(argv), "exit_code": code,
                        "duration_s": round(time.time() - t0, 2), "output_tail": tail})
    ev = gather_evidence(root)
    ev["basis"] = "executed commands"
    ev["checks_run"] = results
    ev["passed"] = bool(results) and all(r["exit_code"] == 0 for r in results)
    if framework:
        ev["test_framework"] = framework
    return ev


def deploy_readiness(root: str, run_checks: bool = False) -> dict:
    if not os.path.isdir(root):
        return {"error": f"not a directory: {root}"}
    stack = detect_stack(root)
    scripts = node_scripts(root) if stack == "node" else {}
    checks = {
        "stack_detected": stack != "unknown",
        "has_readme": _exists(root, "README.md", "README", "readme.md"),
        "has_tests": _has_tests(root, stack, scripts),
        "has_dockerfile": _exists(root, "Dockerfile"),
        "has_dockerignore": _exists(root, ".dockerignore"),
        "has_ci": _has_ci(root),
        "has_env_template": _exists(root, ".env.example", ".env.sample"),
        "has_gitignore": _exists(root, ".gitignore"),
    }
    # parsed evidence, not file existence: package.json must actually declare scripts.start
    checks["has_start_script"] = stack == "node" and bool(str(scripts.get("start") or "").strip())
    recs = []
    if not checks["has_dockerfile"]:
        recs.append("Add a Dockerfile (use scaffold with the matching kind for a template).")
    if not checks["has_tests"]:
        recs.append("Add tests — deployable means verifiable.")
    if not checks["has_start_script"] and stack == "node":
        recs.append('package.json has no "start" script — add one (e.g. node server.js).')
    if not checks["has_ci"]:
        recs.append("Add a CI workflow (.github/workflows/ci.yml) to test+build on push.")
    if not checks["has_env_template"]:
        recs.append("Add .env.example documenting required env vars (never commit real secrets).")
    if not checks["has_readme"]:
        recs.append("Add a README with run + deploy instructions.")
    score = round(100 * sum(1 for v in checks.values() if v) / len(checks))
    out = {"stack": stack, "score": score, "checks": checks, "recommendations": recs,
           "evidence": gather_evidence(root)}
    out["evidence"]["note"] = ("readiness scores inventory, not behavior — call deploy_verify "
                               "for executed-command evidence bound to this commit")
    if run_checks:
        out["evidence"]["executed"] = verify(root)
    return out


# ----------------------------------------------------------- scaffolds
def _w(root, rel, content):
    full = os.path.join(root, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)
    return rel


_CI = """name: ci
on: [push, pull_request]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: build & test
        run: |
          {build}
"""


def _plan(kind: str) -> list[tuple[str, str]]:
    """The (rel_path, content) plan for each scaffold kind — computed, not written."""
    plan: list[tuple[str, str]] = []
    if kind == "node-api":
        plan += [("package.json",
                  '{\n  "name": "app",\n  "version": "0.1.0",\n  "type": "module",\n'
                  '  "scripts": { "start": "node server.js", "test": "node --test" },\n'
                  '  "engines": { "node": ">=20" }\n}\n'),
                 ("server.js",
                  'import http from "node:http";\n'
                  'const PORT = process.env.PORT || 3000;\n'
                  'const server = http.createServer((req, res) => {\n'
                  '  if (req.url === "/healthz") { res.writeHead(200); return res.end("ok"); }\n'
                  '  res.writeHead(200, { "content-type": "application/json" });\n'
                  '  res.end(JSON.stringify({ message: "hello from app", path: req.url }));\n'
                  '});\n'
                  'export { server };\n'
                  'if (import.meta.url === `file://${process.argv[1]}`) server.listen(PORT, () => console.log(`:${PORT}`));\n'),
                 ("server.test.js",
                  'import { test } from "node:test";\nimport assert from "node:assert";\n'
                  'import http from "node:http";\nimport { server } from "./server.js";\n'
                  'test("healthz returns ok", async () => {\n'
                  '  await new Promise(r => server.listen(0, r));\n'
                  '  const { port } = server.address();\n'
                  '  const body = await new Promise((res) => http.get(`http://127.0.0.1:${port}/healthz`, (r) => {\n'
                  '    let d = ""; r.on("data", c => d += c); r.on("end", () => res(d)); }));\n'
                  '  assert.equal(body, "ok");\n  server.close();\n});\n'),
                 ("Dockerfile",
                  "FROM node:20-alpine\nWORKDIR /app\nCOPY package.json ./\nCOPY . .\n"
                  "EXPOSE 3000\nHEALTHCHECK CMD wget -qO- http://localhost:3000/healthz || exit 1\n"
                  'CMD ["node", "server.js"]\n'),
                 (".github/workflows/ci.yml", _CI.format(build="npm test"))]
    elif kind == "python-api":
        plan += [("app.py",
                  'import json, os\nfrom http.server import BaseHTTPRequestHandler, HTTPServer\n\n'
                  'class H(BaseHTTPRequestHandler):\n'
                  '    def do_GET(self):\n'
                  '        if self.path == "/healthz":\n'
                  '            self.send_response(200); self.end_headers(); self.wfile.write(b"ok"); return\n'
                  '        self.send_response(200); self.send_header("content-type","application/json"); self.end_headers()\n'
                  '        self.wfile.write(json.dumps({"message":"hello from app","path":self.path}).encode())\n'
                  '    def log_message(*a): pass\n\n'
                  'def make(port=0):\n    return HTTPServer(("0.0.0.0", port), H)\n\n'
                  'if __name__ == "__main__":\n    make(int(os.environ.get("PORT", 8000))).serve_forever()\n'),
                 ("test_app.py",
                  'import threading, urllib.request, unittest\nfrom app import make\n\n'
                  'class T(unittest.TestCase):\n'
                  '    def test_health(self):\n'
                  '        s = make(0); port = s.server_address[1]\n'
                  '        threading.Thread(target=s.handle_request, daemon=True).start()\n'
                  '        self.assertEqual(urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz").read(), b"ok")\n\n'
                  'if __name__ == "__main__":\n    unittest.main()\n'),
                 ("requirements.txt", "# stdlib only — add deps here\n"),
                 ("Dockerfile",
                  "FROM python:3.12-slim\nWORKDIR /app\nCOPY . .\nEXPOSE 8000\n"
                  "HEALTHCHECK CMD python -c \"import urllib.request;urllib.request.urlopen('http://localhost:8000/healthz')\" || exit 1\n"
                  'CMD ["python", "app.py"]\n'),
                 (".github/workflows/ci.yml", _CI.format(build="python -m unittest"))]
    elif kind == "static":
        plan += [("index.html",
                  "<!doctype html><meta charset=utf-8><title>app</title><h1>hello from app</h1>\n"),
                 ("Dockerfile", "FROM nginx:alpine\nCOPY . /usr/share/nginx/html\nEXPOSE 80\n"),
                 (".github/workflows/ci.yml", _CI.format(build="echo 'static — nothing to test'"))]
    else:
        return None
    plan += [(".dockerignore", ".git\nnode_modules\n.venv\n__pycache__\ndist\n"),
             (".env.example", "# required environment variables\nPORT=3000\n"),
             (".gitignore", "node_modules\n.venv\n__pycache__\n.env\ndist\n")]
    return plan


def scaffold(root: str, kind: str, *, replace: bool = False, dry_run: bool = False) -> dict:
    plan = _plan(kind)
    if plan is None:
        return {"ok": False, "error": f"unknown kind {kind!r}; use node-api | python-api | static"}

    conflicts = [rel for rel, _ in plan if os.path.exists(os.path.join(root, rel))]
    if dry_run:
        return {"ok": True, "dry_run": True, "kind": kind, "root": root,
                "would_write": [rel for rel, _ in plan], "conflicts": conflicts,
                "note": "pass replace=true to overwrite conflicts (originals saved as *.engine-ai.bak)"}
    if conflicts and not replace:
        return {"ok": False, "error": "refusing to overwrite existing files",
                "conflicts": conflicts,
                "hint": "re-run with replace=true (originals are preserved as *.engine-ai.bak) "
                        "or dry_run=true to preview"}

    os.makedirs(root, exist_ok=True)
    written, backups = [], []
    for rel, content in plan:
        full = os.path.join(root, rel)
        if os.path.exists(full):  # replace mode: preserve the original first
            bak = rel + ".engine-ai.bak"
            with open(full, encoding="utf-8", errors="ignore") as src, \
                    open(os.path.join(root, bak), "w", encoding="utf-8") as dst:
                dst.write(src.read())
            backups.append(bak)
        written.append(_w(root, rel, content))
    if not os.path.exists(os.path.join(root, "README.md")):
        written.append(_w(root, "README.md",
                          f"# app ({kind})\n\nScaffolded by engine-ai. Deployable skeleton with a "
                          f"/healthz endpoint, tests, Dockerfile, and CI.\n\n## Run\n\nSee Dockerfile / CI.\n"))
    return {"ok": True, "kind": kind, "root": root, "files": written, "count": len(written),
            "replaced": bool(backups), "backups": backups}
