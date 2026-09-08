# sptmod — Architecture and Session Context

This document is the authoritative context for anyone (human or AI session)
picking up this project fresh. It describes the ACTUAL state of the code,
including every place where reality forced a deviation from the original
specification. Read order for a new session:

1. This file (current truth).
2. `docs/FORGE_API.md` (verified Forge API contract; supersedes HANDOFF §3a).
3. `docs/HANDOFF.md` (product specification in English; History section lists
   changes from the original German version; conflicts defer to this file or
   FORGE_API.md).

## What sptmod is

A mod manager for Single Player Tarkov (SPT) that turns the game directory
into a git repository with a whitelist `.gitignore`. Every mod install is one
commit with structured trailers; remove = revert, disable = revert, enable =
cherry-pick. Player profiles are versioned together with mods so both roll
back as one. Git is the only source of truth about the installation — there
is no separate registry. Targets Linux and Windows; git must never surface in
user-facing language (the Windows audience does not know git).

Stage 1 (this repo): Python core + typer CLI + Tauri desktop app (RPC) + Svelte 5
frontend. Stage 2 (not started): port core+CLI to Rust. **The pytest suite is
the specification for that port** — this is why tests are strict, black-box,
and more important than any individual implementation choice.

## Repository layout

```
src/sptmod/
  core/                 pure business logic; knows neither CLI nor HTTP
    errors.py           typed exception hierarchy (single source of failures)
    models.py           shared dataclasses, trailer key constants, Progress protocol
    game_dir.py         GameLayout detection, SPT version chain, process check
    launch.py           server/client process start, state file, game watcher
    sptserver.py        local SPT server's launcher API (address, ping, login)
    repo.py             THE git layer; the ONLY module allowed to use subprocess
    archive.py          extraction (.7z/.zip/.rar), traversal guard, anchor search
    modmeta.py          package.json parsing, trailer mapping, npm-style semver
    conflicts.py        file/DLL/dependency/SPT-version checks (HANDOFF §6)
    profile.py          profile residue detection from install commits (§7)
    services/           all user-facing operations as pure functions
      __init__.py       facade: the 34 service functions and their result types
      common.py         shared constants, repo mod lookups, layout path translation
      state.py          status, history, snapshot, installability guard
      gamedir.py        directory browsing/locating, init, SPT version, launch
      profiles.py       profile listing, residue detection, cleanup
      lifecycle.py      remove, disable, enable, adopt
      install_archive.py  install from a local archive, archive metadata
      install_forge.py  Forge install planning and execution
      update.py         update discovery and application
      catalog.py        catalog sync/search/browse, mod details, favorites
      transfer.py       export and import of mod lists
    workspace.py        staging dir for downloads/extraction, apply lock
    favorites.py        list and persist per-user favorited mod IDs
    forge/
      __init__.py       package docstring only
      client.py         the ONLY module allowed to use httpx; Forge v0 client
      catalog.py        SQLite cache (config dir), sync/search/classify
      cache.py          persistent archive cache (download resumption, LRU)
      download.py       streaming downloads, cache hit/resumption, size verification
      resolve.py        install plans over /mods/dependencies
  watch.py              process entry for detached game watcher
  cli.py                typer adapter; exceptions -> exit codes; rich tables
  server/
    __init__.py         RPC and OpenAPI dispatch package
    rpc.py              hidden CLI command `sptmod rpc <name>` (RPC stdin/stdout)
    schemas.py          Pydantic mirror of the core dataclasses (THE contract)
    errors.py           RPC error mapping and exit codes
    dump_openapi.py     python -m sptmod.server.dump_openapi openapi.json
src-tauri/              Tauri v2 desktop app
  src/
    lib.rs              Tauri builder, plugin registration, command generation
    main.rs             application entry point
    rpc.rs              #[tauri::command] generic invoke handler
    driver.rs           spawns `sptmod rpc <name>` as child process
  tests/
    rpc_driver_tests.rs   with mocked subprocess
    rpc_e2e_ignored.rs    against real CLI on _data/fakegame/
  tauri.conf.json       bundle targets/resources, updater endpoint and pubkey
  sidecar/              PyInstaller output, untracked, declared as bundle resource
packaging/              freezing the Python core for the desktop bundle
  sptmod_entry.py       PyInstaller entry point (calls sptmod.cli.main)
  sptmod.spec           one-dir build named sptmod, console mode, UPX off
.github/workflows/
  ci.yml                Python/frontend/Rust gates plus sidecar and app matrices
  release.yml           v* tag -> signed bundles in a draft release
tests/                  Python test suite (1597 tests)
  conftest.py           fixtures: fake_game_dir, fake_launchable_dir, etc.
  fixture_types.py      fixture Protocols for typing
  forge_json.py         Forge API mock payload builders
  pe_builder.py         PE file assembly helpers
  test_version_agreement.py  the version single-source rule
  test_*.py             individual test modules
Cargo.toml              workspace (members: src-tauri)
typings/                local stubs for untyped packages
  rarfile.pyi           stub for rarfile (rar support)
frontend/               SvelteKit, Svelte 5 runes, adapter-static, no SSR
  src/lib/index.ts           re-exports of public API
  src/lib/updateall.ts       bulk update orchestration
  src/lib/gamedir.ts         game directory path resolution
  src/lib/launch.ts          game launch UI logic (localStorage toggle, buttons, poll cadence)
  src/lib/theme.ts           theme preference (system|light|dark), localStorage, apply
  src/lib/window.ts          window chrome (minimize/maximize/close) — Tauri boundary
  src/lib/updater.ts         version, update check/install, relaunch — Tauri boundary
  src/lib/updatestate.ts     update state machine, progress and wording (pure)
  src/lib/styles/tokens.css  design tokens; light on :root, dark on [data-theme='dark']
  src/lib/styles/base.css    reset, native controls, global utility classes
  src/lib/api/transport.ts   the app's `rpc` invoke() and progress listen() site
  src/lib/api/types.ts       GENERATED from openapi.json — never hand-edit
  src/lib/api/client.ts      typed wrappers per RPC function
  src/lib/modfilter.ts       filter and sort predicates; schema for chips, categories
  src/lib/filterstate.ts     module-scope filter state (survives route nav, not restart)
  src/lib/components/        SetupScreen, ModsView, ModRow, Dialog
  src/routes/+layout.svelte  app shell: top bar, nav tabs, window controls
  src/routes/                / (setup or mods), /history, /profile, /settings
docs/HANDOFF.md         product specification (English; History lists changes)
docs/FORGE_API.md       verified API reference (canonical host sp-mod.com)
docs/design/            UI redesign brief, Stripe reference, README of omissions
openapi.json            committed contract dump; frontend types derive from it
_data/mods/             untracked corpus of 21 real mod archives for local runs
```

## Hard invariants (enforced by review and tests)

- `subprocess` only in `core/repo.py` (git operations, always captured and
  waited) and `core/launch.py` (detached game/server processes that outlive the
  call) — one module per concern.
- `httpx` only in `core/forge/client.py` (read-only Forge API with throttling)
  and `core/sptserver.py` (local SPT server, loopback, self-signed cert, zlib) —
  one module per remote service.
- Core raises typed exceptions only; no print/logging outside cli.py/server/.
- One API contract, four transports: service function ↔ `sptmod rpc <name>`
  ↔ Tauri `#[tauri::command] rpc()` ↔ camelCase wrapper in client.ts. Guarded
  by tests: route-per-function, schema-parity, error-mapping completeness in
  `tests/test_rpc_contract.py`; contract drift detection in `tests/test_dump_openapi.py`.
- Contract change loop: edit schemas → `uv run python -m sptmod.server.dump_openapi
  openapi.json` → `pnpm gen:api` in frontend/ → fix client.ts/test literals.
- Three sanctioned Tauri boundaries in the frontend, nothing else imports
  `@tauri-apps/*`: `src/lib/api/transport.ts` is the only site of the app's
  `rpc` `invoke()` and progress `listen()` (`subscribe()` returns
  `Promise<unsubscribe>`); `src/lib/window.ts` covers window chrome only
  (`getCurrentWindow()`: minimize, toggle maximize, close, is-maximized) and
  never calls `invoke('rpc', ...)`; `src/lib/updater.ts` covers self-update only
  (`getVersion`, the updater plugin's `check`/`downloadAndInstall`, the process
  plugin's `relaunch`), with all of its decision logic in the import-free
  `src/lib/updatestate.ts`.
- All paths via pathlib; no Linux-only assumptions.
- English in every generated artifact.
- User-facing text never uses git vocabulary (see auto-snapshot below).

## Game directory model (deviates from HANDOFF — verified against reality)

- Layouts: SPT 4.1+ uses `SPT_Runtime/` as the server dir, 4.0.x used `SPT/`,
  3.x was flat (rejected with a clear message). `game_dir.detect_layout`
  returns a `GameLayout`; ALL paths (mods, profiles, whitelist, install
  targets) derive from it. `BepInEx/` is layout-independent, sits in the root.
- 4.x `core.json` has NO `sptVersion` field. Version chain:
  core.json → PE version resource of `SPT.Server.exe` (stdlib reader, 8 MB
  read cap) → stored answer in the game repo's git config key
  `sptmod.sptversion`. An explicitly passed version (CLI `--spt-version`, API
  param) short-circuits the chain and is stored. CLI/UI prompt exactly once
  when nothing resolves — BEFORE `git init` runs.
- `init` is transactional: it removes a `.git` it created itself on any later
  failure; a zero-commit repo (half init) is resumed; a repo with commits
  raises `AlreadyInitializedError` (`ALREADY_INITIALIZED`, 409) which the
  setup UI treats as "setup done, go to the main view".
- SPT's own client files (`BepInEx/plugins/spt/`,
  `BepInEx/patchers/spt-prepatch.dll`) are never adoption candidates; they
  belong to the baseline commit (constant `SPT_OWN_CLIENT_PATHS`).

## Git layer specifics

- The HANDOFF §5 whitelist gitignore does NOT work verbatim under real git
  (`!dir/` re-includes only the directory entry). `repo.gitignore_whitelist`
  generates per-layout patterns with `**` re-inclusions, including
  `BepInEx/patchers/**`.
- The whitelist tracks the SPT program set too, so an SPT update is one commit
  and a downgrade one revert: `!<runtime>/*` covers every file directly in the
  runtime directory (executables, `.dll`, `.so`, `.json`, `.xml`) and
  `!<runtime>/SPT_Data/**` the static release data (including the ~670 MB
  `database/`), while `<runtime>/SPT_Data/**/cache|logs/` and the unchanged
  `<runtime>/user/*` re-ignore keep caches, logs, certificates, profile
  backups and `.sptmod-*` out. Re-including a directory entry alone tracks
  nothing, so subdirectories no line re-includes stay invisible.
- Migration of an existing repository: `Repo.write_whitelist` rewrites
  `.gitignore` whenever it differs from `gitignore_whitelist(...)`, and both
  `status()` and `ensure_installable` call it before they look at the tree
  (`ensure_installable` before its `is_clean()` fast path, which would
  otherwise never see the newly visible files). The program files then surface
  as untracked; `status` classifies them via `game_dir.is_game_program_path`
  under `game_files_changed` — never as `other_untracked`, never as a
  `ManualMod` adopt hint — so the next mod operation sweeps them, together
  with the rewritten `.gitignore`, into the `snapshot: game files updated`
  commit instead of blocking. Untracked files below mod prefixes keep their
  adopt behaviour.
- Trailers are written via `git interpret-trailers` and read via
  `git log --format=%(trailers:...)`; never regex over the body. Trailer
  values reject `\n`/`\r` (injection guard). Keys per HANDOFF §5 plus
  `Mod-Previous-Version` on updates.
- `commit(paths=…)` stages only those paths; `status()`/`is_clean()` see the
  index column; revert/cherry-pick never absorb untracked files and check the
  `--abort` result. `show_file_content` raises `GitError` on binary content.
- Mod state = the youngest commit carrying the mod's `Mod:` trailer; the
  describing metadata comes from the youngest FULL-trailer commit (revert
  commits carry only `Mod` + `Mod-State`). Documented deviation: `enable`
  cherry-picks the youngest full-trailer commit (current version incl.
  updates), not the literal original install commit.

## Dirty-tree UX (deviates from a literal reading of HANDOFF §4b)

Before any mod operation: game-running check, then — if the tree is dirty —
changed profiles/configs are secured in an AUTOMATIC snapshot commit and the
operation proceeds (that state is the normal aftermath of playing). Two more
kinds of tracked change are secured the same way instead of blocking, each
with its own `StatusReport` field and its own snapshot subject:
`modified_mod_settings` (changed or deleted tracked files below an ACTIVE
mod's directory — the mod rewrote its own config while the game ran) becomes
`snapshot: mod settings changed`, and `game_files_changed` (changed or
deleted tracked files no mod commit accounts for, e.g. `BepInEx/plugins/spt/`
after an SPT update) becomes `snapshot: game files updated`, after which the
SPT version is resolved again and stored in `sptmod.sptversion`. What is left
in `other_modified` — a tampered tracked file no active mod owns, such as a
loose plugin DLL — and untracked manual mod copies still block, with a
plain-language message suggesting adopt. `ensure_installable` in services/state.py.
The explicit `snapshot` service (`sptmod snapshot`, RPC `snapshot`, the
Profiles-tab button) secures the same two classes first, through the shared
`secure_updated_files`, and only then commits the profiles and configs, so
an SPT update the user snapshots is committed instead of being left behind.
`SnapshotResult` reports the three classes separately: `game_files`,
`mod_settings` and `commit`/`files`, the latter empty when no profile or
config changed. Only a call that commits nothing at all raises.
Untracked files an active mod writes inside its own directory (settings a mod
generates while playing) are runtime data: `status` leaves them out of the
report, so they never block and are never snapshotted. Ownership of a path is
the youngest NON-snapshot commit touching it (`last_touching_mod`), so
securing a mod's settings or a game file never takes a directory's owner
away from it. `remove` and `disable`
delete them together with the mod, because a leftover file would otherwise
keep the directory alive and resurface as a foreign manual install.

## Profile residues and cleanup

After a mod is removed or updated, its content may still be referenced in the
player profiles — installed items, accepted quests, trader standing. The
residue detection (§7 of HANDOFF, `check_profiles` service) identifies this
content so the user can remove it before gameplay resumes. This section
documents the structured categories, the two-phase cleanup service, and the
backup / snapshot protocol.

### Residue categories and collection

The category table below names 16 structured residue kinds and one report-only
fallback. Each category specifies the JSON path(s) where the content lives, the
character scope (``pmc``, ``scav``, or shared sections), and the cleanup action.
IDs are collected from db files of the mod's install commit: items (24-character
hex object keys and `_id` entries), traders (directory names below `db/traders/`
and the `_id` of `base.json`), quests (`_id` entries below a `quests` path
segment), recipes (`_id` entries below a `hideout` path segment, plus entries of
`recipes`, `scavRecipes`, `cultistRecipes` dicts), and achievements (`id` entries
in `achievements.json` or files below `achievement`/`achievements` segments).

**The `_tpl` correction:** An early bug read `item.get("tpl")` instead of
`item.get("_tpl")`, so inventory residues were detected only by the string
fallback. The inventory template field in the real profile is `_tpl` (verified
against SPT 4.1.3 live profiles and the server DLL); this fix ensures hits are
found structurally and attributed to the correct profile location.

| Category | Characters | JSON path | Matched against | Action |
|---|---|---|---|---|
| `item` | pmc, scav | `/characters/<c>/Inventory/items` | `items[]._tpl` ∈ ids | remove item and its descendants |
| `encyclopedia` | pmc, scav | `/characters/<c>/Encyclopedia` | dict key ∈ ids | remove key |
| `wishlist` | pmc, scav | `/characters/<c>/WishList` | dict key ∈ ids | remove key |
| `trader` | pmc, scav | `/characters/<c>/TradersInfo` | dict key ∈ traders | remove key |
| `trader-purchase` | — | `/traderPurchases` | dict key ∈ traders | remove key |
| `quest` | pmc, scav | `/characters/<c>/Quests` | `[].qid` ∈ quests | remove entry |
| `repeatable-quest` | pmc | `/characters/<c>/RepeatableQuests` | entry's `_id`/`templateId`/`questStatus.qid` ∈ quests or `traderId` ∈ traders | remove entry |
| `task-counter` | pmc, scav | `/characters/<c>/TaskConditionCounters` | key/value `sourceId` ∈ quests | remove key |
| `hideout-production` | pmc, scav | `/characters/<c>/Hideout/Production` | key/value `recipeId` ∈ recipes | remove key |
| `hideout-area` | pmc, scav | `/characters/<c>/Hideout/Areas` | any `_tpl` ∈ items anywhere below `Areas[].slots[]` | reset slot to `{"locationIndex": n}` |
| `unlocked-recipe` | pmc, scav | `/characters/<c>/UnlockedInfo/unlockedProductionRecipe` | list element ∈ recipes | remove element |
| `insured-item` | pmc, scav | `/characters/<c>/InsuredItems` | `tid` ∈ traders or `itemId` in inventory closure | remove entry |
| `achievement` | pmc | `/characters/<c>/Achievements` | dict key ∈ achievements | remove key |
| `insurance` | — | `/insurance` | entry `traderId` ∈ traders → whole entry; else entry `items[]._tpl` ∈ items → those items + descendants | remove entry or items |
| `dialogue` | — | `/dialogues` | dict key ∈ traders → whole dialogue; else message `templateId` starts with quest id or message `items` hold a matching `_tpl` | remove dialogue or message |
| `userbuild` | — | `/userbuilds` | any `_tpl` ∈ items inside `weaponBuilds`/`equipmentBuilds`/`magazineBuilds` | remove the whole build |
| `string` | — | — | raw-text hit of an id no category attributed | report only, never edited |

Residues are emitted character-major (``pmc``, then ``scav``, then shared
sections) and category-minor in the order above; ``string`` is always last.
``hideout-production``, ``hideout-area``, ``insured-item``, and ``userbuild``
were never observed populated locally; the rules only depend on verified key
names and degrade to "no hit" when a container is missing or of an unexpected
type. ``hideout-area`` deliberately rebuilds the slot from its `locationIndex`
to avoid losing unknown payload data.

### Two-phase cleanup service

`plan_profile_cleanup(path, mod_name)` reads only and reports what a cleanup
would remove; `clean_profile(path, mod_name, profiles=None)` applies it. The
plan is built by the exact code that does the cleaning — a single private
function `clean_document(profile, ids)` produces both the cleaned document and
the report — so the preview never disagrees with the real run.

### Write sequence and backup protocol

When a profile changes, it is backed up to `<runtime>/user/profiles/backups/
<YYYY-MM-DD_HH-MM-SS>/<profileId>.json` (SPT's own convention, UTC timestamp),
then written in place. The backup happens first, so a crash leaves the game's
own backup unaware of the cleanup but post-cleanup recovery is still possible
via git History. Serialization uses `json.dumps(data, ensure_ascii=False,
separators=(",", ":"))` with no indentation or trailing newline, byte-identical
to SPT's own format when the profile is unchanged.

After any profile changes, a snapshot commit is created with the message
`snapshot: cleaned profiles for <mod>` and **no Mod trailers**. A `Mod:` trailer
would make the profile commit look like a mod's own commit, so `current_mods` (services/common.py)
would take it as the mod's head and forget its db files.

### `remove_mod(clean_profiles=True)` ordering

When removing a mod with profile cleanup enabled:
1. Ensure the game is closed and the tree is installable (auto-snapshot dirty
   profiles/configs if needed).
2. Clean the profiles of the mod: collect its ids from the youngest commit
   carrying the full trailer set (not from the revert if the mod was already
   disabled/removed), back up and write changed profiles, snapshot if any
   changed.
3. Revert the mod's install commit with `Mod-State: removed`.
4. Delete runtime data (untracked files the mod wrote while the game ran).

The residue check is skipped: what survives a cleanup is what no cleanup can
attribute (the ``string`` category), and blocking the removal on it would defeat
the purpose. Unattributed ids travel out in `RemoveResult.cleanup.untouched`.

### Deliberately untouched

`Inventory.favoriteItems` and `Inventory.fastPanel` dangling references when
their items are removed are repaired if the items belong to the mod (the items
are deleted, so the references become invalid). Other dangling references stay
in place: `Hideout.Improvements`, customization unlocks, skills, stats, RagFair
info, notes, bonuses, friends, `btrDelivery`, and `spt.mods` are not cleaned
because their ids (improvement ids, customization suite ids, skill names, mod
names) are not collected from the mod db and so a structured claim would be
guesswork. The ``string`` category reports them; they may be removed manually
if needed. No restore service exists — a user can undo a cleanup via git History
by manually reverting the snapshot commit, and the SPT-side backup copy remains
recoverable as long as the server hasn't rotated it past its `maxBackups` limit
(default 15 runs).

## Parallel operations (the apply lock)

Each RPC call is its own process, so several operations can run at once.
Downloads land in a persistent cache (reused across runs), extraction unpacks
into a staging directory below the user cache dir (swept after a day). Only the
final apply step — conflict check, copy/delete, commit — holds an exclusive
lock (`.sptmod-lock` in the game directory, ignored by the whitelist gitignore).
The file is created once and then stays; exclusivity is an operating-system
lock on it (`fcntl.flock`, `msvcrt.locking` on Windows), which the system
releases when the holding process ends, so a crash leaves nothing behind that
blocks — a lock file nobody holds is simply taken. Its JSON content only names
the running operation for the waiter's message. A colliding apply waits (up to
five minutes, reporting that operation through the progress callback) before
raising `OperationInProgressError` (`OPERATION_IN_PROGRESS`); read-only calls
never lock. `ensure_installable` runs inside the lock; its pre-download run
is skipped while another apply holds the lock, because that apply's
not-yet-committed files would look like manual installs.

## Download cache

A persistent cache for downloaded mod archives lives sibling to the staging
directory under `user_cache_path("sptmod")/downloads`. Each archive is keyed by
its Forge `version_id` (globally unique object id); the download `link` is a
redirecting CDN URL and not identity. On-disk layout: `<id>/meta.json` +
`<id>/<archive name>` for complete files, `<id>.part` + `<id>.part.json` for
unclaimed partials, `<id>.<token>.part` for one claimed by a process.

A cache hit skips HTTP and reports "Using the downloaded file". Resume via
`Range` header with 206 response (or 200/mismatch restarts from zero). Integrity
is size-only because the Forge exposes `content_length` (nullable) and no hash;
a mismatching cached file is deleted and fetched once more; a mismatching fresh
download raises `ArchiveError`.

Concurrency: no lock spanning the download — claim by atomic rename of the
`.part`, unique work files, `os.replace` publish; a second finisher overwrites
an identical payload. Cache I/O failures degrade to "no entry" and never surface.

LRU by directory mtime (touched on reuse), default 20 GiB capacity, sweep
after each completed download. Partials older than 7 days are swept. The archive
keeps its real name so extraction messages stay readable.

Implementation: `core/forge/cache.py` and `core/forge/download.py`. `_install_plan` (services/install_forge.py)
temp dir is gone (`install_from_archive` creates its own); `update_mod` keeps its
temp dir for extraction. `clear_catalog_cache` with `downloads: true` wipes the
download cache.

## Archive handling (hardened by a 21-real-mod corpus)

- Anchor search: trusted server prefixes are the exact segments `spt` and
  `spt_runtime` before `user/mods` (NOT `startswith("spt")` — junk like
  `spticons/user/mods` must not anchor). Bare `user/mods` or `plugins`
  anchors need corroborating content (package.json child / a DLL). Real
  server mods often ship WITHOUT package.json — metadata then falls back to
  `--name`/`--version` overrides or the plan entry's version.
- `BepInEx/patchers/` is a recognized client anchor besides `plugins/`.
- A hybrid archive must anchor both halves or fail — never silently install
  the client half only.
- Canonical internal target is `SPT/user/mods`; services translate it to the
  detected runtime dir on copy (`for_game_layout` in services/common.py).
- Extraction: traversal-validated member names for all formats; encrypted
  archives raise typed `ArchiveError`; rar via rarfile with the bsdtar
  backend (rar CREATION is impossible in tests — real samples live in
  `tests/data/`).
- Multi-DLL client name heuristic prefers the DLL whose stem matches the
  archive filename stem.

## Forge integration (docs/FORGE_API.md is authoritative)

- Canonical host `https://sp-mod.com/api/v0` (the Forge MOVED there;
  forge.sp-tarkov.com is defunct). `SPTMOD_FORGE_URL` env var overrides,
  explicit `base_url` beats the env var. **No authentication exists** — the
  v0 API is public read-only; every HANDOFF mention of login/tokens/keyring
  is obsolete.
- Client: token-bucket throttle (default 200/min vs the server's 300/min),
  429 backoff honoring Retry-After, guarded pagination (repeat-URL set +
  `current_page >= last_page`), envelope/error mapping, streaming with
  `follow_redirects=True` (download links 307-redirect to CDNs/GitHub).
- Catalog cache: SQLite in the platformdirs config dir (never the game dir).
  Schema evolution = `_MOD_COLUMN_MIGRATIONS` (mods) + `_VERSION_COLUMN_MIGRATIONS`
  (versions) ALTER lists applied on open; the cache is disposable. Cached per
  mod: thumbnail, teaser, favourites, endorsements, category title, license
  name+link, source links (JSON column), featured/ads/AI flags, the reduced
  description plus the `updated_at` it was fetched for, and `first_seen_at`
  (UTC ISO-8601 Z, stamped on first insert, preserved across upserts, NULL for
  pre-column rows = never "new"). Per version: `fika_compatibility` (nullable;
  `compatible|incompatible|unknown`), filled only by `fetch_versions` (include=versions
  summaries carry none; `_merge_version` preserves stored values). Delta sync
  via `filter[updated_between]` (one-day overlap, idempotent upserts); a FULL
  sync prunes rows the API no longer returns — but never on an empty response.
  `DATA_VERSION` stays "2" (no resync forced). Unicode-correct casefold search;
  `%`/`_` literal. Both `CatalogMod` and `CatalogVersion` schemas expose all fields.
- Compatibility filter: an EMPTY `spt_version_constraint` counts as
  INCOMPATIBLE (old hub imports would otherwise flood the filter — 749 false
  positives measured). Install-time §6 checks stay tolerant (warning).
- `resolve.plan_install`: `/mods/dependencies` per-pair value IS the direct
  dependency-node list (`[]` = none; an OMITTED pair = unresolvable target).
  Dependencies-first ordering, diamond dedup, cycle detection, fail-closed
  `_is_satisfied` on unparseable versions. Plan entries with `link=None`
  (sync-only cache) get links via `catalog.fetch_versions` automatically.
- `install_mod`: one commit per plan entry with `Mod-Forge-Id`,
  `Mod-Forge-Version-Id`, `Mod-Source`=URL; `Mod-Version` falls back to the
  plan entry's version for metadata-less archives (check_updates needs valid
  `id:version` pairs). Mid-plan failure raises `PartialInstallError`
  (`PARTIAL_INSTALL`, 409, `details.installed`). The plan is re-resolved at
  execution (documented TOCTOU acceptance).
- `update_mod`: ONE commit replacing the tracked files, subject
  `mod: NAME OLD → NEW`, `Mod-Previous-Version`, `BepInEx/config/` untouched,
  clean-tree guard runs before the Forge query.
- `get_mod_details`: cached mod + versions + description (HTML stripped to
  text server-side — never render Forge HTML). The description is served from
  the cache while its stored stamp equals the mod row's `updated_at`, so a
  restart costs no request; a sync that moved the mod on triggers exactly one
  refetch. Degrades to the stored (or empty) description when the Forge is
  down.
- Catalog-unavailable (unwritable config dir etc.) is a typed
  `CatalogUnavailableError` (`CATALOG_UNAVAILABLE`, 503); export degrades to
  a linkless list instead of failing.
- `list_favorites` and `set_favorite(mod_id, favorite)`: Forge mod ids marked as
  favorites, stored in `favorites.json` in the platformdirs config dir (same
  location as the catalog SQLite db), outside every game directory. The file is
  NOT part of the disposable cache — it survives `clear_catalog_cache` and
  applies across all game folders. Marking is independent of the catalog: any
  Forge id is accepted and duplicate marks are idempotent. Returns the full id
  list. Frontend joins the favorites set with browse results by id, so a
  favorited-but-uncached mod displays as a favorite without a catalog entry.

## Launching the game

All three game-launch service functions (`launch_profile`, `launch_status`,
`stop_game`) delegate to `core/launch.py` and `core/sptserver.py`. The sequence
is: port probe via GET `/launcher/v2/ping` → start `SPT.Server.*` if the port is
free → poll readiness every 1 second with a 120-second bound → PUT
`/launcher/v2/login` (zlib-compressed body, `responsecompressed: 0` header) →
spawn the game client with the exact argv, cwd and environment from the official
launcher's logic → spawn a detached watcher that polls the process table every
3 seconds and closes the server when the game closes (if configured).

**State file:** `<game dir>/.sptmod-launch.json` holds the running process
identities (server, client, watcher) along with `profile_id`,
`stop_server_on_exit` and `failure` (empty unless the watcher gave up waiting
for the game). It is read and written atomically (mkstemp + os.replace,
the `favorites.save_favorites` pattern) and is a hint only — the process table
is the source of truth. Located in the game directory because every short-lived
process involved (launch call, status call, watcher) learns about the game
directory alone.

**Launch log:** `<game dir>/.sptmod-launch.log`, appended per launch and per
watcher decision, in plain language. It holds the server command and working
directory, the readiness poll summary (checks, seconds waited, last answer),
the login result, the client command with the `-token=` value replaced by
`<hidden>`, the environment keys sptmod set (values only for `WINEPREFIX` and
`PROTONPATH`) and the watcher's decisions; when the watcher gives up it also
appends the `game_processes()` snapshot and the wider
`game_dir.spt_process_sightings()` list (every process whose name or command
line mentions `tarko`/`spt.server`, with its first command-line token). The
client's stderr is redirected into this file, so a wrapper that refuses to
start says why. Every write is wrapped so `OSError` can never fail a launch,
and the file is trimmed to 200 KB from the front, dropping whole lines. A
client wrapper that exits within `CLIENT_EXIT_GRACE_SECONDS` (0.5 s) with a
non-zero code raises `LaunchError` naming the log file.

**Watcher:** Started as `python -m sptmod.watch <game directory>` (detached,
never awaited), it lives as long as the game does. Uses `sys.executable` so no
PATH or environment lookup is needed. Polls every 3 seconds and counts the game
as running when either the recorded client `(pid, started_at)` is alive (fast
path) or `game_dir.game_processes()` reports a process of kind `client`. This
matters on Linux, where the spawned pid is the umu/Proton wrapper that may exit
or be replaced while `EscapeFromTarkov.exe` keeps running under Wine. The
spawned-pid check is deliberately weaker than the one `launch_status` and
`stop_game` use: a live pid counts even when the recorded start time does not
compare equal, because stopping the server under a player in a raid is the far
worse error than a watcher that waits too long.

After the launch the watcher waits up to a 120-second grace period for a game
process to appear (both grace period and poll interval are injectable). If the
grace period elapses with no game and the spawned pid is gone as well, the
launch counts as failed: the server is closed under the usual rules and `failure` is
written to the state file (the file is kept, not cleared). If the spawned pid is
still alive it *is* the game or its wrapper, so the watcher keeps waiting on it
regardless of the grace period. Once a game has been seen, the watcher waits for
the last game process to disappear, then closes the server (if the launch set
`stop_server_on_exit` and the server was started by sptmod, not reused) and
clears the state file.

**Server reuse:** `launch_profile` will reuse a server that is already running
unless it fails to answer or the port is held by a different program. An
already-running server is never stopped by the watcher (only one started by sptmod
is). This allows testing or background server runs.

**Client arguments (identical on both platforms):**
```
-force-gfx-jobs native -token=<profile_id>
-config={'BackendUrl':'<base_url>','Version':'live','MatchingVersion':'live'}
```

The profile ID is the profile file's stem (== `info.id` in the profile JSON); the
login username is read from `info.username`; the display name for progress is
`characters.pmc.Info.Nickname`.

**Linux spawn:** via umu, with the absolute path of `EscapeFromTarkov.exe` as
umu's first argument (as the official launcher passes it), and environment
variables from `LauncherSettings.json`
(`WINEPREFIX`, `PROTONPATH`, env vars from `LaunchSettings` tokens and
`DefaultEnv`, plus `__NV_DISABLE_EXPLICIT_SYNC=1` which the official launcher
sets process-wide). `tokenize_launch_settings` mirrors the launcher's
`TokenizeLaunchSettings`: both quote characters open a run, `\"`/`\'` are
literal quotes, an unterminated run reaches the end, an empty run still yields
a token; a `DefaultEnv` value keeps its surrounding quotes stripped (either
kind). The working directory is the game root. If `LauncherSettings.json` is missing or unusable on
Linux, `LauncherSettingsError` is raised (Windows has no equivalent check).

**Windows spawn:** plain `EscapeFromTarkov.exe` with the working directory as the
game root. Process flags are `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP |
CREATE_NO_WINDOW` so nothing blocks and the process does not outlive the parent
console window.

**Detached spawn:** Both platforms spawn with all standard streams as DEVNULL so
no reader is needed. On POSIX, `start_new_session=True` (setsid) puts the child
in its own session; on Windows, the flags above have the same effect. This means
the game survives the exit of the short-lived RPC process and never receives
signals bound to that process group.

**Detecting the running game:** `game_dir.game_processes()` classifies a
process three ways, in order: its name contains a known name (substring,
case-insensitive), its name equals the first 15 characters of one (Linux
truncates `comm`, so the Proton client shows up as `EscapeFromTarko`), or the
program of its command line matches after splitting on both `/` and `\` —
the Proton client's `argv[0]` is `X:\Games\SPT\EscapeFromTarkov.exe`, which
`os.path.basename` does not split. A process whose command line cannot be read
is skipped. Since the spawned pid is only the wrapper on Linux, this scan is
not a fallback but the second half of the detection.
`stop_game` closes every scanned game process, not only the first, because a
Wine-hosted game appears next to the program that started it.

**What sptmod deliberately does not do:** Pre-launch cleanup (the launcher
deletes `BattlEye/`, `Logs/`, `ConsistencyInfo`, and others before every start;
a user must run the official launcher once to set up `LauncherSettings.json`
anyway, so cleanup can be done outside sptmod). Server log tailing or console
capture. Profile management beyond login. Multi-server support. The `LauncherSettings.json`
field `Servers[]` is ignored; only the local server is addressed.

**Error codes:** Four new exceptions map to four RPC codes — `LaunchError`
(`LAUNCH_FAILED`, server/binary missing, spawn failed, readiness timeout,
profile file unusable), `ServerPortBusyError` (`SERVER_PORT_BUSY`, port held by
a non-SPT program), `LoginFailedError` (`LOGIN_FAILED`, server does not know the
profile), `LauncherSettingsError` (`LAUNCHER_SETTINGS_MISSING`, launcher settings
missing or unreadable, Linux only). "Game already running" reuses the existing
`GameRunningError` (`GAME_RUNNING`).

**Frontend:** Three new routes in the Profiles tab (Launch/Stop buttons per
readable profile with phase-progress display) and the Mods view (running-game
banner, mod-row lock while game is up). Settings page gains a "Launching" section with
one checkbox: "Stop the server when the game closes" (persisted in localStorage,
default true, passed as a parameter to each launch call).

## Desktop app (Tauri) and RPC

- The Tauri v2 desktop app runs `sptmod rpc <name>` as a child process,
  marshaling JSON request/response over stdin/stdout. Exit code 0 = success;
  1 = core exception mapped to RPC error code and details; 2 = request error
  (validation, unknown function). Child binary in this order: env `SPTMOD_CLI`
  when set and non-empty, else the bundled sidecar under the app's resource
  directory when it exists, else `sptmod` on PATH (`uv tool install
  --editable .`). See "Packaging and updates".
- RPC response envelope: `{event:"result",data:T}` or `{event:"error",data:{error,code,details}}`.
  Long ops emit zero or more `{event:"progress",...}` lines (ProgressEvent fields);
  `operation_id` is generated and echoed back. Progress and final result/error lines
  are NDJSON on stdout; stderr carries only free-form diagnostics (tracebacks) and
  is never parsed.
- Rust-only error codes: `unknown_function` (not in FUNCTIONS list), `unreachable`
  (binary not found / spawn failed), `bad_output` (no parseable final result line).
- RPC functions: 34 service functions (declared as `FUNCTIONS: [&str; 34]` in
  rpc.rs); the frontend client.ts has 31 camelCase wrappers (`adopt_mod` is
  RPC-exposed but has no wrapper or frontend caller).
- Plugins: dialog (native folder/save/open), fs (write text file), opener (URLs),
  clipboard-manager (copy), plus process and updater registered in `setup`.
  Capabilities in `src-tauri/capabilities/default.json`.
- Setup flow is ONE step (path — no login). Folder picking: native OS dialog
  via Tauri plugin → name+fingerprint → `locate_directory` search; fallback
  `list_directories` browser; text input always works. `ALREADY_INITIALIZED`
  proceeds straight in.
- Mods view: filter bar of AND-combined toggle chips (New = first_seen_at
  within 14 days; Featured; Installed includes disabled; Favorited; Update
  available; Disabled; Compatible with SPT version client-side over unfiltered
  browse call; Fika compatible per-version flag or mod-level fallback; Client/
  Server/Hybrid mod types from installed mod metadata; category dropdown;
  text search client-side; sort; "Clear filters"), counts show leave-one-out.
  Filter state in module (survives route nav, not app restart); default
  compatible-only. Sortable column headers, thumbnails/teaser, install plan
  confirmation (mod + deps + sizes), RPC progress via Tauri event listener,
  force-retry dialogs rendering conflicts/residues/partial installs. Rows linked
  to installed mods BY FORGE ID first (trailer name ≠ catalog display name!),
  name fallback; all actions use the installed trailer name.
- Detail dialog (click the mod name): stats, category/flag tags, license and
  source links, dates, description, version table with per-version Fika
  compatibility (compatible/incompatible shown as tags).
- Mods view shows a result count line ("N mods") above the table.
- Settings page: sections Game directory, Appearance (theme select: system,
  light, dark), Launching, Export the mod list, Import a mod list, Catalog cache
  (two actions: clear cache alone, or clear cache + remove downloaded mod
  files). A left sub-nav links the sections by hash; the active entry follows
  `page.url.hash`.

## Packaging and updates

### The Python core as a PyInstaller sidecar

- The bundle ships the Python core frozen by PyInstaller (`packaging/sptmod.spec`,
  entry `packaging/sptmod_entry.py`), built into `src-tauri/sidecar/` (untracked)
  with `uv sync --group build` followed by `uv run pyinstaller packaging/sptmod.spec
  --noconfirm --distpath src-tauri/sidecar --workpath packaging/build`.
- ONE-DIR, not one-file: measured 277 ms per RPC call against 427 ms for the
  one-file build, which unpacks itself on every spawn (61 MB vs 27 MB on disk).
  Console mode is mandatory — a windowed build has no stdin/stdout pipes.
- A one-dir build is several files and `externalBin` takes exactly one file per
  target triple, so the sidecar travels as a bundle resource:
  `"resources": {"sidecar/sptmod": "sidecar"}` → `$RESOURCE/sidecar/sptmod[.exe]`
  next to its `_internal/`. tauri-build copies resources into the cargo target
  dir and `resource_dir()` points at the exe dir there, so `cargo tauri dev` and
  an installed bundle resolve the same path. A missing `src-tauri/sidecar/` is a
  hard build error (ResourcePathNotFound) — plain `cargo build` fails until the
  sidecar has been built once.
- Resolution order (`driver::resolve_binary`, pure and unit-tested): `SPTMOD_CLI`
  when set and non-empty, then the sidecar below the resource dir when it exists,
  then `sptmod` on PATH. The env var stays first so the documented escape hatch
  keeps working in shipped builds; `rpc()` supplies
  `app.path().resource_dir().ok()`. On Windows `driver::call` spawns with
  `CREATE_NO_WINDOW` (0x0800_0000), otherwise every RPC call flashes a console.
- The sidecar does not cover `git` (repo.py shells out to it; the deb declares it
  as a dependency, Windows needs Git for Windows on PATH) and no rar backend
  (`unrar`/`bsdtar`, see `archive.RAR_INSTALL_HINT`).

### One version, two files

`[project].version` in `pyproject.toml` is the source of truth. `src/sptmod/__init__.py`
`__version__` (which `sptmod version` prints) and `[package].version` in
`src-tauri/Cargo.toml` must equal it, and `src-tauri/tauri.conf.json` must NOT
carry a `version` key — Tauri v2 falls back to the crate version only while that
key is absent. `tests/test_version_agreement.py` enforces all four conditions.
`frontend/package.json` is private and stays out of the rule.

### Bundles

`bundle.active` is true with targets `deb`, `appimage`, `nsis`, `msi` and
`createUpdaterArtifacts: true`. The bundler signs updater artifacts for AppImage,
msi and nsis only, so a deb install downloads the AppImage artifact and rejects
it ("invalid updater binary format"); `updatestate.describe` turns exactly that
failure into "Automatic updates are only available in the AppImage build —
download the new version from the releases page."

### Updater

- `tauri-plugin-updater` and `tauri-plugin-process` are target-gated dependencies
  and registered inside `setup`, each non-fatally: a plugin that fails to
  initialise prints one line to stderr and the app runs on without update checks.
- Permissions: `updater:allow-check`, `updater:allow-download-and-install` and
  `process:allow-restart` in `capabilities/default.json`; `getVersion()` needs
  nothing beyond `core:default`.
- No raw plugin text reaches the window: `updatestate` matches the plugin's Rust
  error strings against a pattern table and renders a plain sentence per case
  (deb package mismatch, unreachable release feed, no artifact for this system,
  signature/verification), with a generic "could not be downloaded" fallback for
  everything unrecognised.
- A checked `Update` owns a Rust-side handle. `updater.ts` closes the one it
  holds before it stores the result of a new check and again after an install,
  and a failing `close()` is swallowed so it cannot turn a successful check or
  install into an error.
- `plugins.updater.endpoints` is
  `https://github.com/OWNER/REPO/releases/latest/download/latest.json` and
  `plugins.updater.pubkey` holds a PLACEHOLDER key. Both must be replaced before
  the first real release (README "Release"); until then a check simply fails,
  which the UI treats as any other failed check.
- `+layout.svelte` runs one check on mount and renders `startupNotice(state)` —
  a non-blocking line linking to `/settings#updates`, empty for every state but
  `available`, so a failed startup check is silent. The Updates section of
  Settings shows the installed version, the manual check, download progress and
  a restart button once an update is installed.

### CI

`.github/workflows/ci.yml` runs on push and pull request with six jobs: `python`
(pytest --cov, ruff, mypy, pyright, pylint), `frontend` (check, vitest with
coverage, build), `sidecar` (matrix Linux/Windows: build the frozen CLI, then
smoke-test `sptmod version`, `rpc list_directories` and an offline
`rpc search_catalog` against a scaffolded game directory, upload as artifact),
`rust` (needs the Linux sidecar artifact, `cargo build` + `cargo test`), `app`
(matrix Linux/Windows, needs sidecar and frontend, full `tauri build`) and
`workflows` (actionlint). `rust` and `app` download the sidecar artifact and
restore its exec bit because upload-artifact drops file permissions; the app
build overrides `bundle.createUpdaterArtifacts` to false, since the CLI aborts
without a signing key. `app` uploads the resulting packages (deb and AppImage on
Linux, msi and nsis on Windows) as `bundle-<os>`, kept for seven days — unsigned
test builds of that commit, not release artifacts.

Linux-only by design, and therefore never proven on Windows CI: the pytest suite
(POSIX shell stubs, `chmod 0o000` cases, `/proc`) and its 100% coverage gate (the
`sys.platform == "win32"` exclusion only covers the excluded clause itself), plus
`cargo test` (the Rust test files use `std::os::unix`). Windows CI proves that the
sidecar builds and runs and that the desktop app compiles and bundles. Only CI can prove the
Windows installers, `CREATE_NO_WINDOW`, and the signed `latest.json` that
`.github/workflows/release.yml` produces; a real self-update has not been
exercised yet.

`release.yml` triggers on `v*` tags, builds the two platforms with
`max-parallel: 1` (tauri-action rewrites `latest.json` read-merge-delete-upload,
so parallel platforms would drop each other's entries) and uploads installers and
manifest into a draft release. Two guards run before anything is built: the job
fails while `plugins.updater.endpoints` still contains `OWNER/REPO` or
`plugins.updater.pubkey` is still the throwaway placeholder key, and it fails
when the tag is not `v` plus the `pyproject.toml` version.

## Frontend design system

The redesign brief and its visual reference live in `docs/design/`; the README
there lists what is implemented and what is not. What exists:

- **Tokens.** `src/lib/styles/tokens.css` is the single source of colours,
  spacing, type, radii, shadows and z-indices as CSS custom properties. The
  light palette sits on `:root`, the dark palette on `[data-theme='dark']`.
  Each block sets `color-scheme` (`light`/`dark`), so native controls and
  scrollbars follow the theme — this is what fixed the unreadable native
  selects in dark mode. Later additions: `--text-base` (14px, the body size),
  `--page-gutter` (32px), `--row-height` (44px), `--z-popover` (60),
  `--subnav-width` (200px) and `--select-arrow`, the inline SVG chevron for
  native selects, defined once per theme.
- **Theming rule.** Components never hard-code colours; they reference tokens.
  Native controls (`input`, `select`, `option`, `checkbox`, `progress`,
  scrollbars, focus rings) are styled once, globally, in `base.css`, together
  with the global utility classes `.btn-*`, `.badge-*`, `.chip`, `.panel*`,
  `.page-*`. Per-component `<style>` blocks carry layout only.
- **Theme state.** `src/lib/theme.ts` holds the preference `system|light|dark`
  under the localStorage key `sptmod.theme`; `resolveTheme` maps `system`
  through `prefers-color-scheme`, `applyTheme` writes `data-theme` on
  `<html>`, `watchSystemTheme` follows OS changes while the preference is
  `system`. Because the layout is prerendered, an inline script in
  `src/app.html` applies the stored (or system) theme before first paint; it
  repeats the key literal since it cannot import from `theme.ts`. The layout
  toggle and the Settings select both apply the theme; each keeps its state in
  sync by observing the `data-theme` attribute (MutationObserver) rather than
  trusting its own last write.
- **App shell.** `src/routes/+layout.svelte` renders the undecorated window:
  the top bar, then the nav row Mods / Profiles / History / Settings (active
  entry = filled pill), then the body — an optional sub-nav column plus the
  routed content. The layout subscribes to the sub-nav channel on mount and
  unsubscribes on destroy; the sub-nav heading is the label of the current nav
  entry. The top bar, brand and spacers carry `data-tauri-drag-region`; the
  buttons do not, so they stay clickable. Window controls go through
  `src/lib/window.ts` and need the four `core:window:allow-*` permissions
  (`start-dragging`, `minimize`, `toggle-maximize`, `close`) in
  `src-tauri/capabilities/default.json`. The SetupScreen renders inside the
  shell like every other route.

### Component map (`src/lib/components/`)

- `TopBar` — brand, game-folder switcher, centered search input, sync button,
  theme toggle, window controls. The input writes into the mods filter after a
  250 ms debounce, is disabled while no view claims search, `/` focuses it from
  anywhere outside a text field, Escape leaves it.
- `GameDirMenu` — shows the last segment of the game folder path; its menu
  shows the full path and a "Change folder" button that emits the request.
- `SubNav` — the left column the layout renders when a page publishes entries;
  a click emits the selection back to that page.
- `PageHeader` — title, optional subtitle, and the `aside`, `secondary` and
  `primary` action snippets.
- `QuickTabs` — the underlined tab row All / Installed / Updates / Favorites /
  New above the mod table.
- `FilterBar` — the active pills, the "Add filter" pill and "Clear filters";
  a pill picked from the add menu is shown with "Select…" until it has a value.
- `FilterPill` — label, current value and a remove button; the value opens a
  popover with the options, their leave-one-out counts and an Apply button.
- `AddFilterPill` — dashed pill whose popover lists the filters not yet active.
- `Popover` — fixed panel placed under its anchor, closes on outside
  pointer-down, returns focus to the anchor when it goes away.
- `ModTable` — header with the select-all checkbox and the sortable columns,
  one `ModRow` per entry, the empty/loading line and the result count.
- `ModRow` — one catalog row with its actions, install plan, detail, file and
  conflict dialogs.
- `SelectionBar` — sits above the table while rows are selected: count, the
  applicable bulk actions, progress text, "Clear selection".
- `StatusBadge` — the badge for a row state, each with its own icon.

### Pure logic (`src/lib/`, unit-tested)

- `shellstate.ts` — the channel between the layout and the pages: stores for
  the sub-nav entries, the search-enabled flag and the game folder path, plus
  signals for sub-nav selection, sync request and change-folder request. A
  subscription delivers the current value immediately and returns an
  unsubscribe function; every page calls it on destroy and resets what it
  published.
- `filterstate.ts` — the single source for search text, pills and quick tabs.
  It holds one `ModFilter` (chips, category, search), defaults to
  compatible-only, survives route changes but not an app restart, and notifies
  subscribers only when the value really differs.
- `filterpills.ts` — the pill definitions (Compatibility, Category, Type,
  Fika, State, Featured), which pills are active for a filter, applying and
  removing a pill, and the quick tabs. Every pill is a radio because chips are
  ANDed; the Compatibility pill shows the SPT version as its label when one is
  known.
- `popover.ts` — the key handling of a popover: Escape closes, Tab and
  Shift+Tab cycle through the focusable elements of the panel.
- `selection.ts` — toggle one row or all, the tri-state header checkbox,
  pruning a selection to the visible rows, the bulk actions a selection offers,
  and `runBulk`, which walks the names one at a time and records failures
  instead of aborting.

### Mods view behaviour

- `ModsView` mirrors `filterstate` into local state and writes changes back,
  publishes the sub-nav entries with the active quick tab, and enables the
  top-bar search while it is mounted.
- A filter change prunes the selection to the rows that stay visible, so a bulk
  action can never touch a mod the user cannot see.
- Bulk actions reuse the per-row RPCs `enable_mod`, `disable_mod` and
  `update_mod` sequentially; there is no multi-row RPC. Two mod operations at
  once would queue on the backend lock for minutes. Failures are collected and
  shown as one line above the table.
- `ModTable` is a container-query container: below 1060px table width the
  Downloads, Updated and Type columns drop out of the header and the rows, and
  the shared `--mods-grid` template shrinks with them.
- `StatusReport` carries an additive `spt_version` field (empty string when
  unknown), filled locally by `resolve_spt_version`. The Mods page takes it
  from the status call it already makes and hands it to the Compatibility pill,
  which then reads "SPT <version>" instead of "Compatible".

**Deferred** (no data or demand for them yet): shadcn-svelte / Tailwind /
TanStack Table, command palette (Ctrl+K), arrow-key row navigation, loading
skeletons, detail side panel (the detail dialog covers it), pagination.

## Testing

**The pytest suite is the Rust-port specification** — tests are black-box,
behavior-focused; do not weaken them to make an implementation fit.

### Quality gates

```bash
uv run pytest                        # Python suite (1597 collected: 1596 passed, 1 skipped)
uv run pytest --cov                  # with coverage gates (line & branch 100%)
uv run pyright                       # strict type checking on src + tests
uv run pylint src tests              # strict linting (design limits to observed maxima)
uv run mypy --strict src tests       # strict typing on both src and tests
cd frontend && pnpm vitest run --coverage  # frontend (502 tests; 100% coverage)
cargo build && cargo test            # Rust (17 driver + 2 ignored e2e)
cd src-tauri && cargo llvm-cov       # Rust coverage
```

### Suite sizes and coverage

- **Python:** 1596 passed, 1 skipped; `uv run pytest --cov` enforces 100%
  coverage (line and branch) on `src/sptmod` (coverage config in pyproject.toml).
  Run `uv run pytest` without --cov for development (no speed penalty, coverage
  excluded from default pytest addopts so single-file runs stay usable).
- **Frontend:** 502 vitest tests in 17 files; strict 100%
  statements/branches/functions/lines on `src/lib/**/*.ts` (minus `*.test.ts` and
  generated `api/types.ts`; v8 provider).
- **Rust (Tauri):** 17 driver tests + 2 ignored e2e tests. rpc.rs's
  `#[tauri::command] rpc()` glue is covered only by the `#[ignore]` e2e test,
  which needs Wry display; the rest of the uncovered lines in `cargo llvm-cov`
  are async resume points it cannot attribute plus the error path in
  `child.wait()`.
- Svelte components and routes have no tests (file-based, not unit-testable
  without environment setup). To add: `@testing-library/svelte` or
  vitest-browser-svelte + jsdom/happy-dom/Playwright in vite.config.ts + Tauri
  `invoke`/`listen` mock layer — out of scope for now.

### Test details and fixtures

- Fixtures in `tests/conftest.py`: `fake_game_dir` (4.0 `SPT/` layout — the
  bulk of the suite), `fake_game_dir_runtime` (4.1 `SPT_Runtime/`),
  `fake_launchable_dir` (4.1 layout + server/client stub scripts + launcher
  settings + profile with login credentials), `clean_repo`, `install_commit`,
  `make_archive` (zip/7z; rar cannot be created — real samples in `tests/data/`,
  plus an encrypted zip). Shared builders: `tests/forge_json.py` (Forge API mock
  payloads), `tests/pe_builder.py` (PE file assembly). Fixture types: `tests/fixture_types.py`
  (Protocols for typed fixtures).
- Every test module, class, and function has a docstring stating setup, assertion,
  and reason. Tests are fully typed via fixture Protocols; NO suppression pragmas
  in src or tests (`type: ignore`, `noqa`, `pylint: disable`, `pragma: no cover`).
  Lint findings are resolved by refactoring; the only configured knobs are pylint
  design limits set to the exact observed maxima and `min-similarity-lines = 24`
  for the CatalogMod dataclass/schema mirror.
- All Forge tests run against respx mocks shaped by docs/FORGE_API.md; the
  suite NEVER hits the network. Live verification happens manually against
  sp-mod.com (full pipeline verified 2026-09-02: sync 1862 mods, SAIN install
  with dependency commits, update flow).
- Launch tests: `tests/test_sptserver.py` (respx mocks: probe states, login
  true/false, zlib decode fallback), `tests/test_launch.py` (black-box: exact
  client argv/cwd/env, readiness poll, server reuse, profile credentials),
  `tests/test_launch_watch.py` (watcher: client pid poll, server lifecycle,
  `started_by_sptmod` flag).
- RPC tests: `tests/test_rpc_contract.py` (route-per-function, schema-parity,
  error-mapping), `tests/test_dump_openapi.py` (contract drift detection).
  Rust: `src-tauri/tests/rpc_driver_tests.rs` (stub-script driven),
  `src-tauri/tests/rpc_e2e_ignored.rs` (real CLI, `#[ignore]`).
- Download cache tests: `tests/test_forge_cache.py` (cache layout, claim/keep/release,
  LRU/part sweep, env cap), `tests/test_forge_download_cache.py` (respx mocks:
  hit/miss, resume 206/200, interruption, size mismatch, sweep).
- Guards worth knowing before "simple" changes: route-per-function,
  dataclass↔schema parity, exact whitelist behavior, trailer roundtrips.

## Environment variables

| Variable | Effect |
|---|---|
| `SPTMOD_FORGE_URL` | Forge API base URL (mirrors/testing) |
| `SPTMOD_CLI` | path to `sptmod` binary (Tauri RPC driver); wins over the bundled sidecar |
| `SPTMOD_DOWNLOAD_CACHE_MAX_GB` | cap for the download cache (default 20, fractional allowed, non-finite/invalid/≤0 falls back) |
| `SPTMOD_SMOKE_GAME_DIR` | Rust e2e test only; override game directory path for `src-tauri/tests/rpc_e2e_ignored.rs` |
| `XDG_CONFIG_HOME` | relocates the catalog cache (platformdirs) |
| `XDG_CACHE_HOME` | relocates the staging dir and download cache (platformdirs) |

## Known limitations / open points

- Stage 2 (Rust core) not started; the pytest suite is its spec.
- Client-DLL metadata (version/GUID) is not extracted from assemblies (by
  design); `--file` installs without metadata are versionless unless
  `--version` is given (CLI prints a hint).
- `.rar` creation is untestable (extraction works via bsdtar).
- sp-mod.com deviates from the old forge source in category/license/
  dependency-embed shapes — the client parses the LIVE shapes (with
  tolerant fallbacks); resolve uses `/mods/dependencies`, which matches.
- Profile cleanup has no user-facing restore path: a cleaned profile can be
  recovered via git History (revert the snapshot commit manually) or from the
  SPT-side backup copy if the server hasn't rotated it past its `maxBackups`
  limit. A `restore_profile` service is a follow-up candidate.
- String residues (ids the cleanup cannot attribute) stay in place after a
  cleanup. They are reported in `cleanup.untouched` and may indicate issues
  with the db file collection or be unrelated id references (skill names,
  locale keys, mod paths).
- Game launch on Windows is untested (Linux build verified; the Proton client
  is detected by truncated name and by the program of its command line, both
  verified against a live Proton launch).
- Pre-launch cleanup and patching (which the official launcher does) are out of
  scope; sptmod starts the game as-is.
