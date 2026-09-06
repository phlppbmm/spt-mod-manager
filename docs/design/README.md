# Design references

- `UI-REDESIGN-HANDOFF.md` — the original brief for the Stripe-dashboard style
  redesign of the frontend.
- `reference-stripe-dashboard.png` — the visual reference the brief points to
  (image credit: mobbin.com).

Implemented in stage 1 (see "Frontend design system" in `../ARCHITECTURE.md`):
design tokens (`frontend/src/lib/styles/tokens.css`), the base stylesheet
(`base.css`), light/dark themes with system default and a top-bar toggle, the
app shell with drag region and window controls, and restyled Mods, Profiles,
History, Settings, Setup and Dialog screens.

Deliberately not implemented:

- shadcn-svelte / Tailwind / TanStack Table — no new dependencies; the app is
  a handful of components over a hand-written CSS token layer.
- Command palette, keyboard shortcut layer, loading skeletons — no demand.
- Detail side panel — a detail dialog already exists (click a mod name).
- Bulk selection and bulk action bar, pagination — the catalog is one scrolling
  list and the RPC contract has no multi-row action.
- Top-bar profile switcher — there is no active-profile concept in the data.
- Global search field — only the mod catalog is searchable; search stays in
  the mods filter bar.
- Filter-pill popovers with checkbox lists and "+ Add filter" — the existing
  toggle chips express the same filter state.
- Left sub-navigation — only on Settings, the one screen with real sections.
