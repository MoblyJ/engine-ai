#!/usr/bin/env node
/* Runs on `npm rm -g mobly-ai` — best-effort disconnect from Claude Code
 * (unlink skills/commands, remove hook + MCP registration). Never fails. */
"use strict";
const { spawnSync } = require("node:child_process");
const path = require("node:path");
const fs = require("node:fs");
const ROOT = path.resolve(__dirname, "..");
try {
  if (fs.existsSync(path.join(ROOT, "install.sh"))) {
    spawnSync("bash", [path.join(ROOT, "install.sh"), "--uninstall"], { cwd: ROOT, stdio: "inherit" });
  }
} catch (_) { /* ignore */ }
process.exit(0);
