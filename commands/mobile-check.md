---
description: Audit & fix mobile responsiveness — session-aware, with evolving memory (uses the mobile-responsive skill).
---

Audit and fix mobile responsiveness for: ${ARGUMENTS:-.}

Run this **session-aware** so mobile decisions persist and evolve:

### 0. Locate the app + recall memory
- Call `app_find({ path: <cwd or $ARGUMENTS> })`. If it matches an app session, use its `path` and
  `keywords`; otherwise use the given path and derive keywords from it.
- Call `memory_context({ keywords: [...,"mobile","responsive"] })` — if there's prior mobile context
  (breakpoints chosen, known problem areas), build on it instead of re-deciding.

### 1–3. Audit → fix → re-audit (mobile-responsive skill)
1. `responsive_audit(path)` → report score + findings (viewport, breakpoints, responsive units,
   flex/grid, images).
2. Offer to fix each gap; on agreement, implement (viewport meta, `@media` 640/768/1024, fluid units,
   flex/grid, tap targets ≥ 44px, `img{max-width:100%}`), then re-run `responsive_audit` (aim ≥ 80).
3. If a browser tool is available (Chrome DevTools MCP / Playwright), verify at phone (390px) and
   tablet (768px) — report horizontal scroll / overflow.

### 4. Save back to the session + memory
- `memory_save({ keywords: [...,"mobile"], context: "<what was fixed + final score>", data: { "responsive_score": <n>, "breakpoints": [...] } })`.
- If an app session matched, `app_update({ id, summary })` — append the mobile status (e.g. "📱 responsive ✓ score 92") to its 2-line summary so `/resume-app` shows it.

Not done until the audit ran, gaps were fixed (or justified), and the result was saved to memory
(+ the session, if any).
