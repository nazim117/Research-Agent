---
name: design
description: Design conventions for this repo's UI surfaces (dashboard React app, extension side panel) — colors, tokens, spacing, radius, icons, fonts, shared CSS classes. Load before creating or editing any component, page, modal, button, form, panel, or stylesheet in clients/dashboard/ or clients/extension/, so new UI matches the existing look instead of introducing a new pattern.
---

# Design conventions

This repo has two UI surfaces: `clients/dashboard/` (React + Vite) and
`clients/extension/` (Chrome MV3 side panel, plain HTML/JS, no build step).
**Dashboard's design
system is canonical for the whole repo.** The extension's current light
theme + emoji icons is outdated drift, not an intentional second design
language — new or edited extension UI should target the same look as
dashboard, adapted only where the lack of a build step forces a different
mechanism. This skill documents what already exists; it does not invent a
new direction.

There is no UI framework in either surface — no Tailwind, no MUI/Radix/
shadcn, no CSS-in-JS. Reuse happens via hand-written CSS with custom
properties (dashboard) or hardcoded values (extension), consumed as plain
class names on native elements — not a React component library. There is
no `components/ui/` folder anywhere in this repo; don't add one to "fix"
this.

## Dashboard (`clients/dashboard/src/`)

### Tokens

`clients/dashboard/src/palette.css` holds raw color swatches — the only place a hex
value should ever be written. `clients/dashboard/src/index.css`'s `:root` maps
those into semantic tokens that components actually use. **Never write a
literal hex/rgb color in a dashboard `.css` or `.jsx` file** — resolve
through an existing semantic token, or if none fits, add a new raw swatch
to `palette.css` and a semantic var in `index.css` that references it,
following the existing naming pattern.

`palette.css` (raw swatches):
```css
--palette-neutral-950: #0a0b0d;  /* canvas */
--palette-neutral-925: #0e0f12;  /* inset */
--palette-neutral-900: #16181c;  /* base / panel */
--palette-neutral-850: #1b1e23;  /* surface */
--palette-neutral-800: #22262b;  /* raised */
--palette-neutral-750: #2a2f36;  /* one step lighter than raised, for hover/pressed states */

--palette-white: #f6f6f7;
--palette-fog:   #dcdce0;
--palette-slate: #b0b4bc;

--palette-frosted-blue:     #a6e1fa;
--palette-frosted-blue-dim: #7fb8d4;  /* darker accent tint for hover/pressed states */

--palette-green:        #3fb950;
--palette-green-strong: #2ea043;
--palette-amber:        #d29922;
--palette-red:           #f85149;
--palette-red-strong:    #e5484d;
```

`index.css` (semantic tokens, built from the above):
```css
--bg-canvas:  var(--palette-neutral-950);
--bg-inset:   var(--palette-neutral-925);
--bg-base:    var(--palette-neutral-900);
--bg-surface: var(--palette-neutral-850);
--bg-panel:   var(--palette-neutral-900);
--bg-raised:  var(--palette-neutral-800);

--border:        rgba(255, 255, 255, 0.14);
--border-subtle: rgba(255, 255, 255, 0.07);

--text:   var(--palette-white);
--text-2: var(--palette-fog);
--text-3: var(--palette-slate);

--accent:        var(--palette-frosted-blue);
--accent-subtle: rgba(166, 225, 250, 0.16);
--green:         var(--palette-green);
--green-subtle:  rgba(63, 185, 80, 0.08);
--amber:         var(--palette-amber);
--amber-subtle:  rgba(210, 153, 34, 0.1);
--red:           var(--palette-red);
--red-subtle:    rgba(248, 81, 73, 0.08);

--r-sm:   6px;
--r:      10px;
--r-lg:   16px;
--r-xl:   20px;
--r-pill: 999px;

--t: 150ms ease;   /* the one and only transition-duration token — use it for every transition */
```

Panels are Material-3-style **neutral** surfaces — don't color-code panels
against each other. Hierarchy comes from gap/elevation/radius, not fill
color. `--accent` is used sparingly (focus rings, active states, links),
never as a whole-panel fill.

Subtle/alpha variants (`--accent-subtle`, `--green-subtle`, etc.) are
hand-written `rgba(...)` next to their solid counterpart, not derived via
`color-mix()`. Follow that pattern for any new subtle variant.

There is **no spacing scale** — spacing is hand-picked px values inline per
rule. Don't invent a spacing scale; match nearby values in the file you're
editing.

Dashboard is **dark-only** (`color-scheme: dark` in `index.css`) — no light
mode, no theme toggle to support.

### Shared primitives (CSS classes, defined in `clients/dashboard/src/App.css`)

Reuse these classes on plain elements instead of writing a new one-off class
for a pattern that already exists:

- `.btn`, `.btn-primary`, `.btn-secondary`, `.btn-sm`, `.btn-block` — button primitive
- `.input`, `.input.textarea` — input primitive
- `.icon-btn` (`.danger` modifier) — icon-only button
- `.modal-overlay` / `.modal` / `.modal-header` / `.modal-title` / `.modal-body` / `.modal-close` — modal primitive
- `.section` / `.section-title` / `.section-badge` — panel section primitive
- `.toast` / `.toast.success` / `.toast-msg` / `.toast-close` — toast primitive
- `.pane-header` / `.pane-title` — panel header primitive
- small utilities: `.row-gap-sm`, `.col-gap-sm`, `.flex-1`, `.mt-8`, `.mt-12`, `.mb-8`, `.ml-4`, `.ml-auto`, `.input-sm`, `.link-btn`, `.visually-hidden-input`

If a new component needs a variant of one of these (e.g. a new modal), reuse
the base class and add a modifier class next to the existing rule, rather
than duplicating the whole block.

### CSS organization

`App.css` is organized into named sections with divider comments:
```css
/* ─── Chat ────────────────────────────────────────── */
```
New component styles get their own named section in this style, appended
at the end of the relevant file — don't scatter new rules into an unrelated
section.

### Icons

No icon library dependency. `clients/dashboard/src/icons.jsx` hand-authors stroke
SVGs, all spreading a shared `base` props object:
```js
const base = {
  width: '1em', height: '1em', viewBox: '0 0 24 24',
  fill: 'none', stroke: 'currentColor', strokeWidth: 2,
  strokeLinecap: 'round', strokeLinejoin: 'round',
  'aria-hidden': 'true', focusable: 'false',
};
```
New icons follow this exact pattern (24x24 viewBox, `currentColor` stroke,
`strokeWidth: 2`, round caps/joins) and export as a named `Icon*` function
from `icons.jsx`. Never use emoji for UI icons in dashboard, never add a new
icon library dependency. Icon *sizing* is controlled by CSS in `App.css`'s
"Icons" section (e.g. `.btn svg { width: 13px; height: 13px; }`), not by
the icon component itself.

### Typography

Inter, loaded via Google Fonts `@import` at the top of `index.css`:
```css
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
```
`font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;`
for body text. Monospace/code text uses
`font-family: 'JetBrains Mono', ui-monospace, monospace` — note that
JetBrains Mono is **not actually imported anywhere**, so it silently falls
back to `ui-monospace`. Don't propagate this gap: if you add new
code/monospace UI, either use `ui-monospace, monospace` honestly, or add a
real `@import` for JetBrains Mono — don't assume it's already loaded.

### Layout

Main layout is a CSS Grid (`.app` in `App.css`) with a three-pane
NotebookLM-style structure (left sources / center chat / right studio),
panes styled as floating rounded panels (`border-radius: var(--r-lg)`,
`margin: 8px`, `border: 1px solid var(--border-subtle)`) on `--bg-canvas`.
Full-page overlays (Setup Wizard, Settings) use `position: fixed; inset: 0;`
with a fixed-width rail + flex content split — see `wizard-overlay`/
`settings-overlay` in `wizard.css`/`settings.css` for the pattern to copy
for any new full-page overlay.

## Extension (`clients/extension/sidepanel.html`)

Plain HTML/JS with a single inline `<style>` block — no build step, no
React, no way to import dashboard's CSS/JSX files directly. New or edited
extension UI should still target dashboard's dark look, adapted to this
surface's constraints:

- **Tokens**: copy dashboard's token *values* (not a file reference) into
  extension's own `:root` inside its `<style>` block as CSS custom
  properties, then reference `var(--...)` in extension's rules exactly like
  dashboard does. Use the literal values quoted above (`--bg-canvas`,
  `--text`, `--accent`, `--border`, `--r`, etc.) — don't re-derive or
  approximate them.
- **Icons**: no build step means `icons.jsx` components can't be imported.
  New icon needs should use inline `<svg>` markup following the same
  convention (24x24 viewBox, `stroke="currentColor"`, `stroke-width="2"`,
  round caps/joins) instead of adding new emoji glyphs (extension currently
  uses emoji — 🧠 ＋ 🗑 ⚙ ⟳ 📋 📄 📝 — which is exactly the outdated
  pattern to move away from on new/edited UI, not to copy).
- **Font**: adopt Inter via the same Google Fonts `@import`, instead of
  extension's current system-font stack (`-apple-system, BlinkMacSystemFont,
  "Segoe UI", sans-serif`).
- **Scope**: this brings extension's *new/edited* UI in line incrementally.
  A full pass converting all of extension's existing rules (still on its
  old light palette — `#f9f9f9` background, `#1a1a2e` header, `#222` text)
  to the shared tokens is separate future work — don't assume it's already
  done, and don't block a small UI change on doing the full migration
  first.

## When nothing here covers what you're building

Default to matching the nearest existing analogous component's actual
values (open the closest similar file and copy its pattern) rather than
introducing a new convention.
