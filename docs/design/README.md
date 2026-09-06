# Design references

- `UI-REDESIGN-HANDOFF.md` — the original brief for the Stripe-dashboard style
  redesign of the frontend.
- `reference-stripe-dashboard.png` — the visual reference the brief points to
  (image credit: mobbin.com).

Implemented in stage 1 (see "Frontend design system" in `../ARCHITECTURE.md`):

- App shell: a top bar with the game-folder switcher, a centered search field
  bound to the mods filter, a sync button and the light/dark toggle; below it
  the nav row Mods / Profiles / History / Settings with the active entry as a
  filled pill.
- Sub-navigation beside the content on Mods (All mods, Installed, Updates,
  Favorites) and Profiles (one entry per profile). Settings keeps its own
  section list linked by hash.
- Page headers with a title and up to two actions (primary and secondary).
- Quick-filter tabs above the mod table: All, Installed, Updates, Favorites,
  New.
- Filter pills with option popovers, an "Add filter" pill for the filters that
  are not in use yet, and "Clear filters".
- A selection column in the mod table with a bulk action bar for the selected
  mods.
- Status badges with an icon per state.
- Light theme as the default, dark derived from it; both set `color-scheme`,
  so native controls follow.
- Keyboard: `/` focuses the search field, Escape closes a popover or leaves
  the search field, Tab and Shift+Tab cycle inside an open popover.
- Design tokens (`frontend/src/lib/styles/tokens.css`), the base stylesheet
  (`base.css`) and the restyled Mods, Profiles, History, Settings, Setup and
  dialog screens.

Still not implemented:

- shadcn-svelte / Tailwind / TanStack Table — no new dependencies; the app is
  a handful of components over a hand-written CSS token layer.
- Command palette (Ctrl+K).
- Arrow-key navigation between table rows.
- Loading skeletons — an empty table shows a short loading line instead.
- Detail side panel — a detail dialog already exists (click a mod name).
- Pagination — the catalog is one scrolling list.
