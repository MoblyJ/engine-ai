#!/usr/bin/env node
/* engine-ai CLI — connect / disconnect the toolkit to Claude Code.
 * Thin Node wrapper around install.sh so `npm i -g engine-ai` works cross-shell. */
"use strict";
const { spawnSync } = require("node:child_process");
const path = require("node:path");
const fs = require("node:fs");

const ROOT = path.resolve(__dirname, "..");
const SH = path.join(ROOT, "install.sh");

function have(cmd) {
  return spawnSync(process.platform === "win32" ? "where" : "command", ["-v", cmd],
    { shell: true, stdio: "ignore" }).status === 0;
}
function runInstall(args) {
  return spawnSync("bash", [SH, ...args], { cwd: ROOT, stdio: "inherit" }).status || 0;
}

function claudeMissingMessage() {
  console.error(`
\x1b[31m✗ Claude Code was not found on this system.\x1b[0m
engine-ai plugs into Claude Code, so install that first:

    npm install -g @anthropic-ai/claude-code
    # or:  curl -fsSL https://claude.ai/install.sh | bash

Then connect engine-ai:

    engine-ai connect
`);
}

const cmd = (process.argv[2] || "help").toLowerCase();

switch (cmd) {
  case "connect":
  case "install": {
    if (!have("claude")) { claudeMissingMessage(); process.exit(1); }
    if (!have("python3")) { console.error("\x1b[31m✗ python3 not found.\x1b[0m engine-ai's tools run on Python 3 — install it and retry."); process.exit(1); }
    process.exit(runInstall(process.argv.includes("--import-siblings") ? ["--import-siblings"] : []));
  }
  case "uninstall":
  case "disconnect":
    process.exit(runInstall(["--uninstall"]));
  case "doctor": {
    const claude = have("claude"), py = have("python3");
    console.log("engine-ai doctor:");
    console.log(`  Claude Code : ${claude ? "\x1b[32m✓ found\x1b[0m" : "\x1b[31m✗ missing → npm i -g @anthropic-ai/claude-code\x1b[0m"}`);
    console.log(`  python3     : ${py ? "\x1b[32m✓ found\x1b[0m" : "\x1b[31m✗ missing\x1b[0m"}`);
    console.log(`  package     : ${ROOT}`);
    console.log(`  server file : ${fs.existsSync(path.join(ROOT, "mcp/forge_mcp.py")) ? "\x1b[32m✓\x1b[0m" : "\x1b[31m✗\x1b[0m"}`);
    if (claude) spawnSync("claude", ["mcp", "list"], { stdio: "inherit" });
    process.exit(claude && py ? 0 : 1);
  }
  default:
    console.log(`engine-ai — Claude Code toolkit

Usage:
  engine-ai connect            wire engine-ai into Claude Code (skills, commands, MCP tools)
  engine-ai connect --import-siblings   also import skills from ../agent-skills ../gstack ../oh-my-pi
  engine-ai uninstall          remove it from Claude Code
  engine-ai doctor             check prerequisites + connection status

After connecting, open a NEW Claude Code session and try:  /new-app  ·  /mobile-check  ·  /ship-live`);
    process.exit(0);
}
