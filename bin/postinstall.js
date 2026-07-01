#!/usr/bin/env node
/* Runs automatically after `npm install`. Auto-connects to Claude Code.
 * NEVER fails the npm install: if Claude Code is missing, prints a clean
 * message and exits 0 so the package still installs. */
"use strict";
const { spawnSync } = require("node:child_process");
const path = require("node:path");

// Skip during local dev installs of the package's own devDeps / CI, if flagged.
if (process.env.ENGINE_AI_SKIP_POSTINSTALL) process.exit(0);

const ROOT = path.resolve(__dirname, "..");
const have = (c) => spawnSync("command", ["-v", c], { shell: true, stdio: "ignore" }).status === 0;

console.log("\n\x1b[36m◈ engine-ai\x1b[0m — connecting to Claude Code…");

if (!have("claude")) {
  console.log(`
\x1b[33m! Claude Code not found — engine-ai is installed but not yet connected.\x1b[0m
  Install Claude Code, then run  \x1b[1mengine-ai connect\x1b[0m :

      npm install -g @anthropic-ai/claude-code
      engine-ai connect
`);
  process.exit(0); // do not fail npm install
}
if (!have("python3")) {
  console.log("\x1b[33m! python3 not found — install Python 3, then run: engine-ai connect\x1b[0m");
  process.exit(0);
}

const status = spawnSync("bash", [path.join(ROOT, "install.sh")], { cwd: ROOT, stdio: "inherit" }).status;
if (status === 0) {
  console.log("\n\x1b[32m✓ engine-ai connected.\x1b[0m Open a NEW Claude Code session and try  /new-app\n");
} else {
  console.log("\n\x1b[33m! auto-connect didn't finish — run  engine-ai connect  to retry.\x1b[0m\n");
}

// Warn if the npm global bin dir isn't on PATH (why `engine-ai: command not found` happens).
try {
  const prefix = (spawnSync("npm", ["prefix", "-g"], { encoding: "utf8" }).stdout || "").trim();
  const binDir = prefix ? path.join(prefix, "bin") : "";
  const onPath = binDir && (process.env.PATH || "").split(path.delimiter).includes(binDir);
  if (binDir && !onPath) {
    console.log(`\x1b[33m! The 'engine-ai' command may not be found — npm's global bin isn't on your PATH.\x1b[0m
  The Claude Code integration still works. To enable the 'engine-ai' helper command, add this to ~/.bashrc:

      export PATH="${binDir}:$PATH"

  then:  source ~/.bashrc     (or open a new terminal).
  You can always run it directly:  node "${path.join(binDir, "engine-ai")}" doctor  —  or:  npx engine-ai doctor
`);
  }
} catch (_) { /* ignore */ }

process.exit(0); // always succeed the install
