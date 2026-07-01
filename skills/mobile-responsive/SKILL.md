---
name: mobile-responsive
description: Audit and fix a web app's mobile responsiveness — viewport, breakpoints, fluid layout, touch-friendly UI — and verify at phone/tablet viewports. Use when building or reviewing any frontend/UI/website, or when the user mentions mobile, responsive, phone, or small-screen. Uses the mobly-ai MCP tool `responsive_audit`.
---

# Mobile Responsive

A site isn't done until it works on a phone. This skill audits responsiveness, fixes the gaps, and
verifies at real mobile viewports.

## When to use
- Building or reviewing any web UI / landing page / frontend app.
- The user says "mobile", "responsive", "phone", "tablet", "small screen".
- NOT for backend-only / API projects (no UI to test).

## Workflow

```
1 AUDIT ──▶ 2 FIX ──▶ 3 VERIFY (mobile viewports) ──▶ 4 RE-AUDIT
```

### 1. Audit
Call the MCP tool `responsive_audit(path)`. It scores the project and reports gaps:
viewport meta, `@media` breakpoints, responsive units (%/vw/rem/em), flex/grid, responsive images.

### 2. Fix the gaps
- **Viewport:** ensure `<meta name="viewport" content="width=device-width, initial-scale=1">`.
- **Breakpoints:** add `@media (max-width: 640px)` (phone) and `768px`/`1024px` (tablet/desktop).
- **Fluid layout:** replace fixed `width: 1200px` with `max-width` + `%`/`fr`; prefer flex/grid.
- **Touch targets:** interactive elements ≥ 44×44px; spacing for thumbs.
- **Images/media:** `img { max-width: 100%; height: auto; }`, use `srcset` where it matters.
- **Text:** relative units (`rem`), avoid horizontal scroll.

### 3. Verify at mobile viewports
Render and look — don't assume. Options, best first:
- **Chrome DevTools MCP** (if available): open the page, set device emulation (e.g. iPhone 390×844,
  iPad 768×1024), screenshot, check no horizontal scroll / overlap / tiny tap targets.
- **Playwright** (if installed): launch chromium with `viewport: {width:390,height:844}` and
  `isMobile:true`, screenshot key pages.
- **Manual:** in the browser, DevTools → device toolbar (Ctrl+Shift+M) at 390px and 768px.

### 4. Re-audit
Run `responsive_audit` again; aim for score ≥ 80 with no findings, plus clean screenshots at
phone + tablet widths.

## Red flags
- Horizontal scrollbar on a phone width · fixed-px layouts · tap targets < 44px · no viewport meta ·
  text that requires pinch-zoom · images overflowing the screen.

## Verification
- [ ] `responsive_audit` score ≥ 80, no findings
- [ ] No horizontal scroll at 390px (phone) and 768px (tablet)
- [ ] Tap targets ≥ 44px; text readable without zoom
- [ ] Screenshots captured at phone + tablet viewports
