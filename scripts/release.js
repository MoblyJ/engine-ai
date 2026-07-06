#!/usr/bin/env node
/* engine-ai release helper.
 *
 * Bumps package.json's version, commits it, tags it, and pushes both to
 * origin — so `npm install -g MoblyJ/engine-ai` / `npm update -g` (which
 * installs straight from the git repo, no npm-registry publish involved)
 * always resolves to a real, tagged version instead of an untracked commit.
 *
 * Usage:
 *   npm run release -- patch "fix mobile audit false positive"
 *   npm run release -- minor "add /expert citations"
 *   npm run release -- major "rework MCP tool surface"
 */
"use strict";
const { execFileSync } = require("node:child_process");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..");

function sh(cmd, args, opts = {}) {
  return execFileSync(cmd, args, { cwd: ROOT, encoding: "utf8", ...opts }).trim();
}
function die(msg) {
  console.error(`\x1b[31m✗ ${msg}\x1b[0m`);
  process.exit(1);
}
function warn(msg) {
  console.log(`\x1b[33m! ${msg}\x1b[0m`);
}
function firstLine(err) {
  return String((err && err.message) || err).split("\n")[0];
}

const [bump, ...descParts] = process.argv.slice(2);
const description = descParts.join(" ").trim();

if (!["patch", "minor", "major"].includes(bump)) {
  die(`usage: npm run release -- <patch|minor|major> "description"\n  got bump: ${bump || "(none)"}`);
}
if (!description) {
  die('a description is required, e.g.  npm run release -- patch "fix X"');
}

// --- guards --------------------------------------------------------------
const status = sh("git", ["status", "--porcelain"]);
if (status) {
  die("working tree is not clean — commit or stash your changes before releasing:\n" + status);
}

const branch = sh("git", ["rev-parse", "--abbrev-ref", "HEAD"]);
if (branch !== "main") {
  die(`release must be run from 'main' (currently on '${branch}')`);
}

sh("git", ["fetch", "origin", "main", "--tags"]);
const local = sh("git", ["rev-parse", "HEAD"]);
const remote = sh("git", ["rev-parse", "origin/main"]);
if (local !== remote) {
  die("local main is not in sync with origin/main — pull/push first");
}

// --- bump + commit + tag (local only, rollback-able while local) ---------
const newVersion = sh("npm", ["version", bump, "--no-git-tag-version"]).replace(/^v/, "");
const tag = `v${newVersion}`;
const commitMsg = `${tag}: ${description}`;

try {
  sh("git", ["add", "package.json"]);
  sh("git", ["commit", "-m", commitMsg]);
} catch (e) {
  // package.json was bumped on disk but never committed — restore it so
  // the tree is clean again and the next attempt starts from scratch.
  try { sh("git", ["checkout", "--", "package.json"]); } catch (_) { /* best effort */ }
  die(`commit failed, rolled back package.json bump: ${firstLine(e)}`);
}

try {
  sh("git", ["tag", "-a", tag, "-m", description]);
} catch (e) {
  die(
    `commit "${commitMsg}" was created locally but tagging failed: ${firstLine(e)}\n` +
    `  Fix the issue and run manually:\n` +
    `    git tag -a ${tag} -m "${description}" && git push origin main && git push origin ${tag}\n` +
    `  or undo the commit entirely with:  git reset --soft HEAD~1 && git checkout -- package.json`
  );
}

// --- push (from here on, local repo has a real commit + tag) -------------
try {
  sh("git", ["push", "origin", "main"]);
} catch (e) {
  die(
    `commit + tag ${tag} exist locally but pushing 'main' failed: ${firstLine(e)}\n` +
    `  Fix the issue (network/auth) and run:  git push origin main && git push origin ${tag}`
  );
}

try {
  sh("git", ["push", "origin", tag]);
} catch (e) {
  die(
    `'main' was pushed but pushing tag ${tag} failed: ${firstLine(e)}\n` +
    `  Fix the issue and run:  git push origin ${tag}`
  );
}

console.log(`\x1b[32m✓ pushed ${tag}\x1b[0m (${commitMsg})`);

// --- GitHub release (best-effort, non-fatal) ------------------------------
try {
  sh("gh", ["release", "create", tag, "--title", tag, "--notes", description]);
  console.log(`\x1b[32m✓ GitHub release ${tag} created\x1b[0m`);
} catch (e) {
  warn(`skipped GitHub release (gh not available/authed): ${firstLine(e)}`);
  warn(`create it later with:  gh release create ${tag} --title ${tag} --notes "${description}"`);
}

console.log(`
Users get this with:
  engine-ai update                             # latest main (now at ${tag})
  npm uninstall -g engine-ai && npm install -g MoblyJ/engine-ai#${tag}   # pinned to this exact release
`);
