"""Deploy-readiness scanner + deployable-app scaffolder.

`deploy_readiness(path)` inspects a project and returns a checklist of what a real
deployment needs (build, tests, Dockerfile, CI, healthcheck, env template, README) so the
agent knows exactly what to add. `scaffold(path, kind)` writes a minimal but genuinely
deployable skeleton (zero-runtime-dependency where possible) for node-api / python-api /
static.
"""

from __future__ import annotations

import os


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


def _has_ci(root):
    wf = os.path.join(root, ".github", "workflows")
    return (os.path.isdir(wf) and any(os.listdir(wf))) or _exists(root, ".gitlab-ci.yml")


def _has_tests(root):
    for dp, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "target", "dist", ".venv"}]
        for f in files:
            lf = f.lower()
            if "test" in lf or "spec" in lf:
                return True
    return False


def deploy_readiness(root: str) -> dict:
    if not os.path.isdir(root):
        return {"error": f"not a directory: {root}"}
    stack = detect_stack(root)
    checks = {
        "stack_detected": stack != "unknown",
        "has_readme": _exists(root, "README.md", "README", "readme.md"),
        "has_tests": _has_tests(root),
        "has_dockerfile": _exists(root, "Dockerfile"),
        "has_dockerignore": _exists(root, ".dockerignore"),
        "has_ci": _has_ci(root),
        "has_env_template": _exists(root, ".env.example", ".env.sample"),
        "has_gitignore": _exists(root, ".gitignore"),
    }
    if stack == "node":
        checks["has_start_script"] = _exists(root, "package.json")
    recs = []
    if not checks["has_dockerfile"]:
        recs.append("Add a Dockerfile (use scaffold with the matching kind for a template).")
    if not checks["has_tests"]:
        recs.append("Add tests — deployable means verifiable.")
    if not checks["has_ci"]:
        recs.append("Add a CI workflow (.github/workflows/ci.yml) to test+build on push.")
    if not checks["has_env_template"]:
        recs.append("Add .env.example documenting required env vars (never commit real secrets).")
    if not checks["has_readme"]:
        recs.append("Add a README with run + deploy instructions.")
    score = round(100 * sum(checks.values()) / len(checks))
    return {"stack": stack, "score": score, "checks": checks, "recommendations": recs}


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


def scaffold(root: str, kind: str) -> dict:
    os.makedirs(root, exist_ok=True)
    written = []
    if kind == "node-api":
        written.append(_w(root, "package.json",
            '{\n  "name": "app",\n  "version": "0.1.0",\n  "type": "module",\n'
            '  "scripts": { "start": "node server.js", "test": "node --test" },\n'
            '  "engines": { "node": ">=20" }\n}\n'))
        written.append(_w(root, "server.js",
            'import http from "node:http";\n'
            'const PORT = process.env.PORT || 3000;\n'
            'const server = http.createServer((req, res) => {\n'
            '  if (req.url === "/healthz") { res.writeHead(200); return res.end("ok"); }\n'
            '  res.writeHead(200, { "content-type": "application/json" });\n'
            '  res.end(JSON.stringify({ message: "hello from app", path: req.url }));\n'
            '});\n'
            'export { server };\n'
            'if (import.meta.url === `file://${process.argv[1]}`) server.listen(PORT, () => console.log(`:${PORT}`));\n'))
        written.append(_w(root, "server.test.js",
            'import { test } from "node:test";\nimport assert from "node:assert";\n'
            'import http from "node:http";\nimport { server } from "./server.js";\n'
            'test("healthz returns ok", async () => {\n'
            '  await new Promise(r => server.listen(0, r));\n'
            '  const { port } = server.address();\n'
            '  const body = await new Promise((res) => http.get(`http://127.0.0.1:${port}/healthz`, (r) => {\n'
            '    let d = ""; r.on("data", c => d += c); r.on("end", () => res(d)); }));\n'
            '  assert.equal(body, "ok");\n  server.close();\n});\n'))
        written.append(_w(root, "Dockerfile",
            "FROM node:20-alpine\nWORKDIR /app\nCOPY package.json ./\nCOPY . .\n"
            "EXPOSE 3000\nHEALTHCHECK CMD wget -qO- http://localhost:3000/healthz || exit 1\n"
            'CMD ["node", "server.js"]\n'))
        written.append(_w(root, ".github/workflows/ci.yml", _CI.format(build="npm test")))
    elif kind == "python-api":
        written.append(_w(root, "app.py",
            'import json, os\nfrom http.server import BaseHTTPRequestHandler, HTTPServer\n\n'
            'class H(BaseHTTPRequestHandler):\n'
            '    def do_GET(self):\n'
            '        if self.path == "/healthz":\n'
            '            self.send_response(200); self.end_headers(); self.wfile.write(b"ok"); return\n'
            '        self.send_response(200); self.send_header("content-type","application/json"); self.end_headers()\n'
            '        self.wfile.write(json.dumps({"message":"hello from app","path":self.path}).encode())\n'
            '    def log_message(self,*a): pass\n\n'
            'def make(port=0):\n    return HTTPServer(("0.0.0.0", port), H)\n\n'
            'if __name__ == "__main__":\n    make(int(os.environ.get("PORT", 8000))).serve_forever()\n'))
        written.append(_w(root, "test_app.py",
            'import threading, urllib.request, unittest\nfrom app import make\n\n'
            'class T(unittest.TestCase):\n'
            '    def test_health(self):\n'
            '        s = make(0); port = s.server_address[1]\n'
            '        threading.Thread(target=s.handle_request, daemon=True).start()\n'
            '        self.assertEqual(urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz").read(), b"ok")\n\n'
            'if __name__ == "__main__":\n    unittest.main()\n'))
        written.append(_w(root, "requirements.txt", "# stdlib only — add deps here\n"))
        written.append(_w(root, "Dockerfile",
            "FROM python:3.12-slim\nWORKDIR /app\nCOPY . .\nEXPOSE 8000\n"
            "HEALTHCHECK CMD python -c \"import urllib.request;urllib.request.urlopen('http://localhost:8000/healthz')\" || exit 1\n"
            'CMD ["python", "app.py"]\n'))
        written.append(_w(root, ".github/workflows/ci.yml", _CI.format(build="python -m unittest")))
    elif kind == "static":
        written.append(_w(root, "index.html",
            "<!doctype html><meta charset=utf-8><title>app</title><h1>hello from app</h1>\n"))
        written.append(_w(root, "Dockerfile", "FROM nginx:alpine\nCOPY . /usr/share/nginx/html\nEXPOSE 80\n"))
        written.append(_w(root, ".github/workflows/ci.yml", _CI.format(build="echo 'static — nothing to test'")))
    else:
        return {"error": f"unknown kind {kind!r}; use node-api | python-api | static"}
    # common files
    written.append(_w(root, ".dockerignore", ".git\nnode_modules\n.venv\n__pycache__\ndist\n"))
    written.append(_w(root, ".env.example", "# required environment variables\nPORT=3000\n"))
    written.append(_w(root, ".gitignore", "node_modules\n.venv\n__pycache__\n.env\ndist\n"))
    if not os.path.exists(os.path.join(root, "README.md")):
        written.append(_w(root, "README.md",
            f"# app ({kind})\n\nScaffolded by mobly-ai. Deployable skeleton with a "
            f"/healthz endpoint, tests, Dockerfile, and CI.\n\n## Run\n\nSee Dockerfile / CI.\n"))
    return {"kind": kind, "root": root, "files": written, "count": len(written)}
