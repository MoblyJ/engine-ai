---
description: Audit and fix the mobile responsiveness of the current web app (uses the mobile-responsive skill).
---

Audit mobile responsiveness for: ${ARGUMENTS:-.}

Follow the `mobile-responsive` skill:
1. Call the MCP tool `responsive_audit` on the path.
2. Report the score + findings (viewport, breakpoints, responsive units, flex/grid, images).
3. Offer to fix each gap; if the user agrees, implement the fixes and re-run `responsive_audit`.
4. If a real browser is available (Chrome DevTools MCP / Playwright), verify at phone (390px) and
   tablet (768px) viewports and report whether there's horizontal scroll or overflow.
