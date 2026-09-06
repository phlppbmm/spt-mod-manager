# SPT Mod Manager — UI Redesign Handoff

## Goal

Replace the current frontend, which looks dated and "traditional", with a fresh, modern data-tool UI. The visual and structural reference is the **Stripe Dashboard** (see `reference-stripe-dashboard.png`, credit mobbin.com). The app is fundamentally about managing lists of mods and filtering them, so the redesign should be optimized for scanning, filtering and acting on rows quickly.

Framework is **Svelte 5** (runes). Keep existing business logic and data layer; this is a presentation-layer rewrite.

## Design direction

Think "Stripe / Linear": light, calm, information-dense but not cramped, lots of whitespace, thin dividers, one accent color, status communicated through small colored badges. Nothing skeuomorphic, no gradients, no heavy borders, no card-in-card nesting.

### Layout (top to bottom)

1. **Top bar** — slim. Left: app name/logo and active profile switcher (dropdown). Center: global search field. Right: primary actions (e.g. "Add mod"), notifications/updates indicator, settings, and the **theme toggle** (see below).
2. **Main navigation** — horizontal tab row under the top bar (e.g. Mods, Profiles, Load Order, Downloads, Settings). Active tab gets a filled pill highlight like Stripe's "Payments".
3. **Content area** with a narrow **left sub-navigation** (section heading + links, active link marked with a vertical accent bar) and the main pane to its right.
4. **Main pane header** — page title left, primary CTA button (accent color) and secondary button (outlined, e.g. Export / Refresh) right.
5. **Quick-filter tabs** — a row of text tabs directly under the title: `All · Enabled · Disabled · Update available · Conflicts`. Underline on the active tab.
6. **Filter bar** — a row of filter pills. Each active pill shows the field and its value inline (`Status | Enabled`, `Category | Weapons`) with an `×` to remove it and a dropdown caret to edit it. A dashed-outline `+ Add filter` pill lets users add more (Category, Author, SPT version, Source, Tags, ...). A `Clear filters` link sits at the far right. Clicking a pill opens a popover with checkboxes and an `Apply` button, exactly like Stripe's "Filter by Status" popover.
7. **Data table** — the centerpiece. Selection checkbox column, then columns like Name, Version, Status badge, Category, Author, SPT compat, Last updated, and a trailing `⋯` row-actions menu. No zebra striping, thin horizontal dividers only, uppercase small-caps column headers, sortable columns. Row hover highlight. Below the table: result count on the left (`42 mods`), pagination or "load more" on the right.
8. **Detail panel** (optional but desired) — clicking a row opens a right-hand side panel or sheet with mod details: description, dependencies, changelog, install path, enable/disable toggle, uninstall.

### Components & styling

- Status badges: small rounded chips with tinted background and matching text color (green = enabled/OK, gray = disabled, amber = update available, red = conflict/error), optionally with an icon.
- Bulk actions appear as a floating bar or in the header when one or more rows are selected (Enable, Disable, Update, Remove).
- Keyboard friendly: `/` focuses search, `⌘/Ctrl+K` opens a command palette for search + actions, arrow keys move through rows, `Esc` closes popovers/panels.
- Empty states and loading skeletons for the table.
- Typography: system UI font stack or Inter; 13–14px base in the table.
- Spacing/radius: consistent 4px grid, 6–8px corner radius, 1px borders in a subtle neutral.

Recommended building blocks: **shadcn-svelte** (Data Table on TanStack Table, Popover, Command, Tabs, Badge, Sheet, Dropdown Menu) on top of Bits UI, with Tailwind. Their "Tasks" example is very close to the target look and can serve as a starting point.

## Theming — dark and light mode (required)

- Both a **light** and a **dark** theme must exist and be equally polished. Dark mode is not just inverted light mode: use layered neutral grays (background < surface < elevated surface), lower-contrast dividers, and slightly desaturated badge colors so they don't glow.
- **Default: follow the system preference** (`prefers-color-scheme`). If the OS switches theme at runtime, the app follows automatically as long as the user hasn't overridden it.
- **Manual toggle in the top bar** with a **sun / moon icon**: sun shown while in dark mode (click → light), moon shown while in light mode (click → dark). A small animated crossfade or rotate between the icons is welcome. Tooltip: "Switch to light/dark mode".
- Theme state has three values: `system | light | dark`. The toggle cycles between explicit light and dark; a "Use system setting" option (e.g. in a small dropdown on the toggle or in Settings) returns to `system`.
- Persist the user's choice (localStorage or the app's settings store) and apply it **before first paint** to avoid a flash of the wrong theme.
- Implementation: all colors via CSS custom properties (design tokens) defined on `:root` for light and on `[data-theme="dark"]` (or `.dark`) for dark. Components never hard-code colors. Use `color-scheme: light dark` so native controls and scrollbars match.
- Check contrast (WCAG AA) for text and badges in both themes.

## Out of scope

- No changes to mod installation logic, file handling or the data model.
- No new features beyond what's needed to present existing data in the new layout (the detail panel and command palette may be stubbed if the data isn't available yet).

## Acceptance checklist

- [ ] Layout matches the structure above (top bar → main nav → sub-nav + main pane → title/CTA → quick-filter tabs → filter pills → table → footer).
- [ ] Filter pills with inline values, popover editing, `+ Add filter`, `Clear filters`.
- [ ] Table with selection, sorting, status badges, row action menu, result count.
- [ ] Light and dark themes, both complete, system preference respected by default.
- [ ] Sun/moon toggle in the top bar, preference persisted, no theme flash on load.
- [ ] Keyboard shortcuts for search, command palette and row navigation.
- [ ] Empty and loading states.
