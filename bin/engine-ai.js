#!/usr/bin/env node
/* engine-ai CLI — connect / disconnect the toolkit to Claude Code.
 * Thin Node wrapper around install.sh so `npm i -g engine-ai` works cross-shell. */
"use strict";
const { spawnSync } = require("node:child_process");
const path = require("node:path");
const fs = require("node:fs");
const os = require("node:os");

const ROOT = path.resolve(__dirname, "..");
const SH = path.join(ROOT, "install.sh");
const PKG = JSON.parse(fs.readFileSync(path.join(ROOT, "package.json"), "utf8"));

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

function cmd_help() {
  console.log(`engine-ai — Claude Code toolkit

Usage:
  engine-ai connect            wire engine-ai into Claude Code (skills, commands, MCP tools)
  engine-ai connect --import-siblings   also import skills from ../agent-skills ../gstack ../oh-my-pi
  engine-ai uninstall          remove it from Claude Code
  engine-ai update             safely update to the latest version (clean uninstall + reinstall)
  engine-ai update 0.13.0      safely update, pinned to a specific published version
  engine-ai doctor             check prerequisites + connection status
  engine-ai knowledge sync     clone + ingest the curated engineering repos into the knowledge store
  engine-ai knowledge status   show ingested domains + chunk counts
  engine-ai knowledge agents   regenerate the domain-expert subagents
  engine-ai --version          print the installed version
  engine-ai --help             show this message

After connecting, open a NEW Claude Code session and try:  /new-app · /expert · /resume-app · /ship-live`);
}

const cmd = (process.argv[2] || "help").toLowerCase();

switch (cmd) {
  case "--version":
  case "-v":
  case "version":
    console.log(PKG.version);
    process.exit(0);
  case "--help":
  case "-h":
    cmd_help();
    process.exit(0);
  case "connect":
  case "install": {
    if (!have("claude")) { claudeMissingMessage(); process.exit(1); }
    if (!have("python3")) { console.error("\x1b[31m✗ python3 not found.\x1b[0m engine-ai's tools run on Python 3 — install it and retry."); process.exit(1); }
    process.exit(runInstall(process.argv.includes("--import-siblings") ? ["--import-siblings"] : []));
  }
  case "uninstall":
  case "disconnect":
    process.exit(runInstall(["--uninstall"]));
  case "update": {
    // Published to the npm registry as the scoped package @okoboji/engine-ai
    // (the bare name "engine-ai" is squatted there by an unrelated package).
    // A full clean uninstall + reinstall (rather than `npm update -g`, or
    // reinstalling in place) remains the most reliable way to guarantee the
    // latest files land — this also mirrors the same defensive pattern used
    // when this package was git-based, kept here as cheap insurance.
    const NAME = "@okoboji/engine-ai";
    const version = process.argv[3]; // optional: `engine-ai update 0.13.0` pins to a specific published version
    const spec = version ? `${NAME}@${version}` : `${NAME}@latest`;
    console.log(`Updating engine-ai (clean uninstall + reinstall ${spec})…`);

    // Deliberately NOT cwd: ROOT here — `pkgDir` below (the directory this
    // command is about to delete) IS ROOT for a global install, so anchoring
    // these spawns to a directory they're about to remove out from under
    // themselves is exactly the self-inflicted cwd hazard this whole file is
    // now defended against elsewhere. os.tmpdir() is stable and unrelated.
    const safeCwd = os.tmpdir();
    spawnSync("npm", ["uninstall", "-g", NAME], { cwd: safeCwd, stdio: "inherit" });

    const prefix = (spawnSync("npm", ["prefix", "-g"], { cwd: safeCwd, encoding: "utf8" }).stdout || "").trim();
    if (prefix) {
      const pkgDir = path.join(prefix, "lib", "node_modules", "@okoboji", "engine-ai");
      if (fs.existsSync(pkgDir)) {
        console.log(`  removing leftover ${pkgDir}`);
        fs.rmSync(pkgDir, { recursive: true, force: true });
      }
      const binLink = path.join(prefix, "bin", "engine-ai");
      try { fs.unlinkSync(binLink); } catch (_) { /* not present */ }
    }

    // --ignore-scripts + explicit connect afterward: registry installs (as
    // opposed to the git-dependency installs this package used to use) have
    // NOT reproduced the ENOENT/uv_cwd lifecycle-script race seen before, but
    // this pattern is cheap insurance and costs nothing to keep.
    const r = spawnSync("npm", ["install", "-g", spec, "--ignore-scripts"], { cwd: safeCwd, stdio: "inherit" });
    if (r.status !== 0) process.exit(r.status || 1);
    process.exit(runInstall([]));
  }
  case "knowledge": {
    const sub = (process.argv[3] || "status").toLowerCase();
    if (!have("python3")) { console.error("\x1b[31m✗ python3 not found.\x1b[0m"); process.exit(1); }
    if (sub === "sync") {
      console.log("Cloning + ingesting the curated engineering repos (this can take a few minutes)…");
      process.exit(spawnSync("python3", [path.join(ROOT, "mcp/knowledge_sync.py"), ...process.argv.slice(4)],
        { cwd: ROOT, stdio: "inherit" }).status || 0);
    }
    if (sub === "agents") {
      process.exit(spawnSync("python3", [path.join(ROOT, "mcp/gen_domain_agents.py")], { cwd: ROOT, stdio: "inherit" }).status || 0);
    }
    // status
    const r = spawnSync("python3", ["-c",
      "import sys,json;sys.path.insert(0,sys.argv[1]);from knowledge import Knowledge;k=Knowledge();" +
      "print('domains:');[print(f\"  {d['domain']:20} {d['chunks']} chunks / {d['repos']} repos\") for d in k.domains()] or print('  (none — run: engine-ai knowledge sync)')",
      path.join(ROOT, "mcp")], { encoding: "utf8" });
    process.stdout.write(r.stdout || r.stderr || "");
    process.exit(0);
  }
  case "doctor": {
    const claude = have("claude"), py = have("python3");
    console.log("engine-ai doctor:");
    console.log(`  version     : ${PKG.version}`);
    console.log(`  Claude Code : ${claude ? "\x1b[32m✓ found\x1b[0m" : "\x1b[31m✗ missing → npm i -g @anthropic-ai/claude-code\x1b[0m"}`);
    console.log(`  python3     : ${py ? "\x1b[32m✓ found\x1b[0m" : "\x1b[31m✗ missing\x1b[0m"}`);
    console.log(`  package     : ${ROOT}`);
    console.log(`  server file : ${fs.existsSync(path.join(ROOT, "mcp/forge_mcp.py")) ? "\x1b[32m✓\x1b[0m" : "\x1b[31m✗\x1b[0m"}`);
    if (claude) spawnSync("claude", ["mcp", "list"], { stdio: "inherit" });
    process.exit(claude && py ? 0 : 1);
  }
  default:
    cmd_help();
    process.exit(0);
}
