#!/usr/bin/env node
/* Runs on `npm rm -g engine-ai` — best-effort disconnect from Claude Code
 * (unlink skills/commands, remove hook + MCP registration). Never fails. */
"use strict";
const { spawnSync } = require("node:child_process");
const path = require("node:path");
const fs = require("node:fs");

// See bin/postinstall.js for why: an invalid process cwd (e.g. npm removed
// the directory this script launched from) can break any spawn that doesn't
// pass an explicit cwd. This script already anchors everything to ROOT and
// passes cwd explicitly to its one spawn call, but guard defensively anyway.
try { process.cwd(); } catch (_) { try { process.chdir(__dirname); } catch (_) { /* best effort */ } }

const ROOT = path.resolve(__dirname, "..");
try {
  if (fs.existsSync(path.join(ROOT, "install.sh"))) {
    spawnSync("bash", [path.join(ROOT, "install.sh"), "--uninstall"], { cwd: ROOT, stdio: "inherit" });
  }
} catch (_) { /* ignore */ }
process.exit(0);
