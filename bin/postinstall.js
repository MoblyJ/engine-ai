#!/usr/bin/env node
/* Runs automatically after `npm install`. Auto-connects to Claude Code.
 * NEVER fails the npm install: if Claude Code is missing, prints a clean
 * message and exits 0 so the package still installs. */
"use strict";
const { spawnSync } = require("node:child_process");
const path = require("node:path");

// Skip during local dev installs of the package's own devDeps / CI, if flagged.
if (process.env.MOBLY_AI_SKIP_POSTINSTALL) process.exit(0);

const ROOT = path.resolve(__dirname, "..");
const have = (c) => spawnSync("command", ["-v", c], { shell: true, stdio: "ignore" }).status === 0;

console.log("\n\x1b[36m◈ mobly-ai\x1b[0m — connecting to Claude Code…");

if (!have("claude")) {
  console.log(`
\x1b[33m! Claude Code not found — mobly-ai is installed but not yet connected.\x1b[0m
  Install Claude Code, then run  \x1b[1mmobly-ai connect\x1b[0m :

      npm install -g @anthropic-ai/claude-code
      mobly-ai connect
`);
  process.exit(0); // do not fail npm install
}
if (!have("python3")) {
  console.log("\x1b[33m! python3 not found — install Python 3, then run: mobly-ai connect\x1b[0m");
  process.exit(0);
}

const status = spawnSync("bash", [path.join(ROOT, "install.sh")], { cwd: ROOT, stdio: "inherit" }).status;
if (status === 0) {
  console.log("\n\x1b[32m✓ mobly-ai connected.\x1b[0m Open a NEW Claude Code session and try  /new-app\n");
} else {
  console.log("\n\x1b[33m! auto-connect didn't finish — run  mobly-ai connect  to retry.\x1b[0m\n");
}
process.exit(0); // always succeed the install
