#!/usr/bin/env node
/* Runs automatically after `npm install`. Auto-connects to Claude Code.
 * NEVER fails the npm install: if Claude Code is missing, prints a clean
 * message and exits 0 so the package still installs. */
"use strict";
const { spawnSync } = require("node:child_process");
const path = require("node:path");
const fs = require("node:fs");

// Skip during local dev installs of the package's own devDeps / CI, if flagged.
if (process.env.ENGINE_AI_SKIP_POSTINSTALL) process.exit(0);

const ROOT = path.resolve(__dirname, "..");

// Pure-Node PATH lookup — deliberately avoids spawning a shell (`command -v`
// via {shell:true}) during postinstall, since that's the exact fragile
// install-time window where shell-spawn races have been observed on WSL2.
function have(cmd) {
  const exts = process.platform === "win32" ? (process.env.PATHEXT || ".EXE;.CMD;.BAT").split(";") : [""];
  for (const dir of (process.env.PATH || "").split(path.delimiter)) {
    for (const ext of exts) {
      try { fs.accessSync(path.join(dir, cmd + ext), fs.constants.X_OK); return true; } catch (_) { /* keep looking */ }
    }
  }
  return false;
}

// Warn if the npm global bin dir isn't on PATH (why `engine-ai: command not found` happens).
// Runs on every exit path, not just the happy path, since it's an orthogonal concern.
function warnIfPathMissing() {
  try {
    const prefix = (spawnSync("npm", ["prefix", "-g"], { encoding: "utf8" }).stdout || "").trim();
    const binDir = prefix ? path.join(prefix, "bin") : "";
    if (!binDir) return;
    const norm = (p) => (p || "").replace(/[\\/]+$/, "");
    const onPath = (process.env.PATH || "").split(path.delimiter).some((d) => norm(d) === norm(binDir));
    if (!onPath) {
      console.log(`\x1b[33m! The 'engine-ai' command may not be found — npm's global bin isn't on your PATH.\x1b[0m
  The Claude Code integration still works. To enable the 'engine-ai' helper command, add this to ~/.bashrc:

      export PATH="${binDir}:$PATH"

  then:  source ~/.bashrc     (or open a new terminal).
  You can always run it directly:  node "${path.join(binDir, "engine-ai")}" doctor  —  or:  npx engine-ai doctor
`);
    }
  } catch (_) { /* ignore */ }
}

console.log("\n\x1b[36m◈ engine-ai\x1b[0m — connecting to Claude Code…");

if (!have("claude")) {
  console.log(`
\x1b[33m! Claude Code not found — engine-ai is installed but not yet connected.\x1b[0m
  Install Claude Code, then run  \x1b[1mengine-ai connect\x1b[0m :

      npm install -g @anthropic-ai/claude-code
      engine-ai connect
`);
  warnIfPathMissing();
  process.exit(0); // do not fail npm install
}
if (!have("python3")) {
  console.log("\x1b[33m! python3 not found — install Python 3, then run: engine-ai connect\x1b[0m");
  warnIfPathMissing();
  process.exit(0);
}

const status = spawnSync("bash", [path.join(ROOT, "install.sh")], { cwd: ROOT, stdio: "inherit" }).status;
if (status === 0) {
  console.log("\n\x1b[32m✓ engine-ai connected.\x1b[0m Open a NEW Claude Code session and try  /new-app\n");
} else {
  console.log("\n\x1b[33m! auto-connect didn't finish — run  engine-ai connect  to retry.\x1b[0m\n");
}

warnIfPathMissing();
process.exit(0); // always succeed the install
