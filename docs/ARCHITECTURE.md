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
    launch/
      __init__.py       facade: the launch names the rest of the tree uses
      launcher.py       one launch: the server, the sign-in, the game client
      spawn.py          starting server/client/watcher in the official launcher's environment
      watch.py          waiting for the game to close, cleanup afterwards
      state.py          the launch state file and the launch log of a game directory
      status.py         what of an installation runs, and closing it again
      process.py        starting, finding and closing the processes of a launch
    sptserver.py        SPT launcher API of this machine or a Fika host (address, ping, profiles, login)
    repo.py             THE git layer; the ONLY module allowed to use subprocess
    archive.py          extraction (.7z/.zip/.rar), traversal guard, anchor search
    modmeta.py          package.json parsing, trailer mapping, npm-style semver
    conflicts.py        file/DLL/dependency/SPT-version checks (HANDOFF §6)
    profile/
      __init__.py       facade: the ten names the rest of the tree uses
      nodes.py          JSON container primitives (lookup, hits, filtered removal)
      ids.py            a mod's content IDs collected from its install commit (§7)
      files.py          profile files: listing, parsing, summaries, atomic write
      categories.py     the residue category table: finder/remover pair per container
      scan.py           residue detection over mods and profiles, document cleanup
    modsets.py          mod set store: one JSON document per user, keyed by game directory
    fika.py             Fika role store (tolerant), canonical host address
    services/           all user-facing operations as pure functions
      __init__.py       facade: the service functions and their result types
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
      modsets.py        mod set CRUD, sharing, profile binding, plan and apply
      fika.py           Fika role of a game folder, profiles of its host
      spt.py            list, apply and undo a published SPT release
    workspace.py        staging dir for downloads/extraction, apply lock
    favorites.py        list and persist per-user favorited mod IDs
    forge/
      __init__.py       package docstring only
      client.py         the ONLY module allowed httpx for the Forge; Forge v0 client
      models.py         the Forge API dataclasses (no httpx, pure data)
      parse.py          tolerant JSON-to-dataclass coercion for Forge payloads
      catalog/          SQLite cache (config dir), sync/search/classify
        __init__.py     facade: CatalogStore and the names callers import
        schema.py       DDL, the MOD_COLUMN_MIGRATIONS ALTER list, default db path
        rows.py         row <-> dataclass mapping for the cached tables
        store.py        the CatalogStore itself: sync, search, browse, favorites
      cache.py          persistent archive cache (download resumption, LRU)
      download.py       streaming downloads, cache hit/resumption, size verification
      thumbnails.py     persistent thumbnail cache; fetches images with the Referer the host demands
      resolve.py        install plans over /mods/dependencies
    sptrelease/
      __init__.py       package docstring only
      client.py         the ONLY module allowed httpx for the SPT release source
      apply.py          pure path/version logic of a release (include set, stale set,
                        required client build, version comparison)
  watch.py              process entry for detached game watcher
  cli/                  typer adapter; exceptions -> exit codes; rich tables
    __init__.py         imports every module below for its command-registration
                        side effect; that import order fixes `sptmod --help`
    base.py             the typer `app`, shared error handling, progress printing
    mods.py             install, update, list, files, remove, disable, enable, adopt
    repo.py             init, status, snapshot, history, export, import
    sources.py          sync, search, browse
    spt.py              the `sptmod spt` group: list, update, downgrade
    gamedir.py          game directory selection commands
    reports.py          check and version
    rpc.py              hidden `sptmod rpc <name>` entry point
  server/
    __init__.py         RPC and OpenAPI dispatch package
    rpc.py              hidden CLI command `sptmod rpc <name>` (RPC stdin/stdout)
    schemas/            Pydantic mirror of the core dataclasses (THE contract)
      __init__.py       facade: the contract names, re-exported unchanged
      base.py           the base model every contract model derives from
      mods.py           models mirroring mods, profiles and the catalog
      requests.py       request/response models of the mod, profile and catalog functions
      modsets.py        mod set models and the models of their service functions
      runtime.py        Fika, game launch and SPT release models
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
  tauri.conf.json       bundle targets, updater endpoint and pubkey
  tauri.bundle.conf.json bundle-only overlay declaring the sidecar resource
  sidecar/              PyInstaller output, untracked, declared as bundle resource
packaging/              freezing the Python core for the desktop bundle
  sptmod_entry.py       PyInstaller entry point (calls sptmod.cli.main)
  sptmod.spec           one-dir build named sptmod, console mode, UPX off
.github/workflows/
  ci.yml                Python/frontend/Rust gates plus sidecar and app matrices
  release.yml           v* tag -> signed bundles in a draft release
tests/                  Python test suite
  conftest.py           git isolation, process-scan guard, game dirs, repos
  fixture_types.py      fixture Protocols for typing
  fixtures_forge.py     Forge client/catalog fixtures (pytest_plugins)
  fixtures_launch.py    launch stubs and process helpers (pytest_plugins)
  fixtures_profile.py   profile document builders (pytest_plugins)
  fixtures_rpc.py       `sptmod rpc` round-trip fixtures (pytest_plugins)
  fixtures_spt.py       SPT release source and archives (pytest_plugins)
  forge_install_env.py  seeded catalogs, dependency nodes and download mocks
  forge_json.py         Forge API mock payload builders
  github_json.py        SPT release source mock payload builders
  launch_helpers.py     launch respx routes, progress capture, poll-until helper
  pe_builder.py         PE file assembly helpers
  profile_cleanup_corpus.py  content IDs and profiles of the cleanup tests
  data/bcj2-*.7z        committed BCJ2 7z samples (py7zr cannot read them)
  test_sptrelease_client.py  release source parsing and failures
  test_services_spt_*.py  releases, apply, refusals and revert of an SPT release
  test_cli_spt.py       `sptmod spt list|update|downgrade`
  test_version_agreement.py  the version single-source rule
  test_*.py             individual test modules
Cargo.toml              workspace (members: src-tauri)
typings/                local stubs for untyped packages
  rarfile.pyi           stub for rarfile (rar support)
  libarchive.pyi        stub for libarchive-c (BCJ2 7z fallback)
frontend/               SvelteKit, Svelte 5 runes, adapter-static, no SSR
  src/lib/index.ts           re-exports of public API
  src/lib/updateall.ts       bulk update orchestration
  src/lib/gamedir.ts         game directory path resolution
  src/lib/launch.ts          game launch UI logic (localStorage toggle, buttons, poll cadence)
  src/lib/spt.ts             SPT version logic (compare, state, apply check, confirmation
                             texts, startup notice, dismissal storage)
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
  src/lib/modsets.ts         mod set rows, options, plan summary, missing-mod install targets
  src/lib/fika.ts            role options, canonical host address, host label, address error
  src/lib/thumbnails.ts      thumbnail loading: session cache, coalesced batches, in-memory LRU
  src/lib/components/        SetupScreen, ModsView, ModRow, Dialog, ModSetEditor, ModSetDialog
  src/routes/+layout.svelte  app shell: top bar, nav tabs, window controls
  src/routes/                / (setup or mods), /history, /profile, /settings
  tests/                     component and route tests (own vitest config, happy-dom)
docs/HANDOFF.md         product specification (English; History lists changes)
docs/FORGE_API.md       verified API reference (canonical host sp-mod.com)
docs/design/            UI redesign brief, Stripe reference, README of omissions
openapi.json            committed contract dump; frontend types derive from it
_data/mods/             untracked corpus of 21 real mod archives for local runs
```

## Hard invariants (enforced by review and tests)

- `subprocess` only in `core/repo.py` (git operations, always captured and
  waited) and `core/launch/` (detached game/server processes that outlive the
  call) — one area per concern.
- `httpx` only in `core/forge/client.py` (read-only Forge API with throttling),
  `core/sptserver.py` (an SPT server — this machine's or a Fika host's — with a
  self-signed cert and zlib bodies) and `core/sptrelease/client.py` (the SPT
  release sources on api.github.com, anonymous, one GET each) — one module per remote
  service. The release *archive* is downloaded through `ForgeClient.stream`,
  which takes an arbitrary URL, so no fourth module appears.
- Core raises typed exceptions only; no print/logging outside cli/ and server/.
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
  runtime directory (executables, `.dll`, `.so`, `.json`, `.xml`),
  `!<runtime>/SPT_Data/**` the static release data (including the ~670 MB
  `database/`), `!BepInEx/core/**` the BepInEx runtime an SPT release ships,
  and the four root-anchored patterns `!/winhttp.dll`,
  `!/doorstop_config.ini`, `!/.doorstop_version`, `!/LICENSE-*.txt` the
  Doorstop loader and the bundled licences (the leading `/` keeps them in the
  game root instead of matching at any depth), while
  `<runtime>/SPT_Data/**/cache|logs/` and the unchanged `<runtime>/user/*`
  re-ignore keep caches, logs, certificates, profile backups and `.sptmod-*`
  out. Re-including a directory entry alone tracks nothing, so subdirectories
  no line re-includes stay invisible.
- `game_dir.is_game_program_path` was widened in lockstep with the whitelist —
  otherwise the newly visible files land in `other_untracked` and block every
  mod operation. It answers true for a file directly in the runtime directory,
  anything below `<runtime>/SPT_Data`, SPT's own client paths
  (`BepInEx/plugins/spt/`, `BepInEx/patchers/spt-prepatch.dll`), anything below
  `BepInEx/core/`, and the four loader files in the game root. The root loader
  files match case-sensitively, byte for byte like the whitelist patterns git
  applies; the SPT client paths keep their case-insensitive match.
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
- Before any git call, `repo.ensure_git_available()` checks that git exists and
  runs (`shutil.which` plus one `git --version` probe, remembered per process;
  `recheck=True` probes again). Without it every entry point raises
  `GitMissingError` (`GIT_MISSING`, CLI exit 17) carrying the install hint.
  It is deliberately no `GitError` subclass, so the handlers that rewrite a
  failed git call into an operation-specific message cannot swallow it; the
  setup screen offers the download link through the opener plugin
  (`frontend/src/lib/githint.ts`).
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
- Catalog cache: SQLite in the platformdirs config dir (never the game dir); the
  default db path lives in `core/forge/catalog/schema.py`, so tests patch
  `XDG_CONFIG_HOME` rather than platformdirs.
  Schema evolution = `MOD_COLUMN_MIGRATIONS` (mods) + `VERSION_COLUMN_MIGRATIONS`
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
- `browse_catalog` judges the entries against the SPT version of the game
  directory unless a caller passes `spt_version=`, which lets the UI ask what
  is ready for a release the player has not installed yet.
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

## Mod thumbnails

The Forge image host `files.sp-mod.com` sits behind Cloudflare hotlink
protection: it answers 403 unless the request carries
`Referer: https://sp-mod.com/`. A webview `<img>` element can never send that
header, so every thumbnail failed. It is hotlink protection, not a CSP
restriction. The images are therefore fetched in Python, where the header can be
set, and handed to the frontend as base64 `data:` URLs.

`core/forge/thumbnails.py` fetches through `ForgeClient.stream` with the Referer
derived from the configured Forge base URL, so an `SPTMOD_FORGE_URL` override
keeps working and no fourth httpx site appears.

Cache: `workspace.thumbnail_cache_dir()` = `<user cache>/sptmod/thumbnails`,
sibling to the download cache. Layout `<key>` for the bytes, `<key>.json` for
`{"url", "media_type"}`, `<key>.part` while writing; the key is the sha256 of the
image URL and the sidecar stores that URL, so a mod whose thumbnail moved misses
instead of being served a stale image. Per-image cap `MAX_IMAGE_BYTES` 4 MiB,
cache cap `MAX_CACHE_BYTES` 128 MiB, LRU by mtime swept after a write.
Disposable like the download cache: every read, write or clean-up failure
degrades to "no thumbnail" rather than failing the view. `clear_catalog_cache`
wipes it too, with no new request or result field.

`get_thumbnails(mod_ids, *, db_path=None, cache_dir=None) -> dict[int, str | None]`
in `services/catalog.py` takes the image URL from the catalog row only, so a
caller can never make the app fetch an arbitrary URL. RPC route
`get_thumbnails`, request `{mod_ids, db_path?, cache_dir?}`, response
`{thumbnails: {"<modId>": "data:…" | null}}` — JSON object keys are strings on
the wire.

Frontend `src/lib/thumbnails.ts`: `loadThumbnail(modId)` over a module-level
session cache (mod id -> data URL or null), per-id in-flight de-duplication and
microtask coalescing into one rpc call, sequential batches of 32, in-memory LRU
of 256 entries. It imports only `api/client.ts`, so `api/transport.ts` stays the
app's only invoke/listen site. A failed batch resolves every id in it to null
and is remembered for the session, so a transient failure shows placeholders
instead of retry storms.

`ModRow` loads the data URL in a `$effect` and renders it in the list row and in
the detail dialog. The letter placeholder of the list row covers "no thumbnail",
"still loading" and "failed" alike, so nothing shows a broken-image icon or
raises a toast; the detail dialog carries no placeholder and renders the image
only once it is loaded.

## Mod sets

A mod set is a named list of mods with the state each of them should have. A
player profile can be bound to one set; launching that profile makes the
installation match the set first. Applying a set is AUTHORITATIVE: mods the set
turns off and active mods the set does not name at all are turned off, so what
runs afterwards is exactly the line-up the set describes. A set never installs,
updates or downgrades anything.

**Storage.** `platformdirs.user_config_path("sptmod", appauthor=False) /
"modsets.json"` (`core/modsets.py`), one document for the user, its top level
keyed by `str(game_dir.resolve())`. Deliberately NOT a file in the game
repository: it would need whitelist changes, would surface in the
`game_files_changed` bucket with its SPT-version side effect, and would compete
with git as the source of truth about the installation. Keying by resolved game
directory keeps the sets of several game folders side by side. Every service
function takes a keyword-only `store_path` seam (tests never touch the real
file). Writes rewrite the whole document (`save_sets`), so the entries of other
game directories survive, through mkstemp + `os.replace` — a crash mid-write
leaves the previous document intact, and the abandoned `.modsets-…` temp file
is never read. Unlike `favorites.py`, an unreadable or malformed store raises
`InvalidMetadataError` naming the file instead of reading as empty: a set
decides which mods a player runs, and silently losing sets is worse than a loud
failure. Unknown keys of a well-formed document are ignored.

On-disk shape:

```json
{"version": 1, "games": {"/home/user/Games/SPT": {
  "sets": [{"name": "Fika night", "created_at": "2026-09-08T18:04:11Z",
            "entries": [{"forge_id": "1234", "name": "SAIN", "version": "3.2.1", "enabled": true},
                        {"forge_id": "", "name": "MyLocalMod", "version": "1.0", "enabled": false}]}],
  "bindings": {"6712a9f0c1d2e3f4a5b6c7d8": "Fika night"}}}}
```

An entry's `name` is the install-commit `Mod:` trailer name, `forge_id` is empty
for mods the Forge knows nothing about, `created_at` is UTC ISO-8601 Z.

**Names.** Compared with `casefold()`, stored with the casing the user typed,
surrounding whitespace stripped; a name that is empty afterwards raises
`InvalidMetadataError`. Saving over an existing set keeps its `created_at`.

**Interchange and share code.** `export_mod_set` renders a set as JSON with
indent 2 and a trailing newline, wrapped under `"mod_set"` (never confused with
`export_mods`' `{"mods": [...]}`); only `name` and `entries` travel, the
importing installation stamps its own `created_at`. The same content is also
returned as a share code: `SPTMODSET1-` + urlsafe base64 of `zlib.compress(...,
9)`, `=` padding stripped. Decoding re-pads with `"=" * (-len(body) % 4)` and
unpacks at most 4 MiB, so a short code cannot claim a large amount of memory;
more content than that, or a damaged code, is an `InvalidMetadataError`.
`import_mod_set` dispatches on the stripped text — `SPTMODSET1-` = code, `{` =
JSON file content, anything else is refused — so file names carry no meaning. A
file that is not UTF-8 is an `InvalidMetadataError` as well.

**Service functions** (`services/modsets.py`, all with keyword-only
`store_path`; `InvalidMetadataError` for an unreadable store is possible in
every one of them and left out of the table):

| Function | Purpose | Errors |
|---|---|---|
| `list_mod_sets(path)` | every stored set plus every profile binding | — |
| `save_mod_set(path, name, entries=None)` | store a set, replacing one of that name | `InvalidMetadataError` (empty name), `GitError` |
| `delete_mod_set(path, name)` | remove the set and unbind the profiles pointing at it | `ModSetNotFoundError` |
| `rename_mod_set(path, name, new_name)` | rename, retargeting the bindings | `ModSetNotFoundError`, `ModSetExistsError` |
| `export_mod_set(path, name)` | file content and share code of the set | `ModSetNotFoundError` |
| `import_mod_set(path, set_file=None, content=None, name=None)` | store a set from a file or a code | `InvalidMetadataError`, `ModSetExistsError` |
| `bind_profile_set(path, profile_id, set_name=None)` | play a profile with a set, or unbind it | `ModSetNotFoundError` |
| `plan_mod_set(path, name)` | read-only report of what applying would change | `ModSetNotFoundError`, `GitError` |
| `apply_mod_set(path, name, ignore_missing=False, progress=None)` | make the mods match the set | `ModSetNotFoundError`, `ModSetIncompleteError`, every error a mod change raises |

Without `entries`, `save_mod_set` captures the installation as it is now: every
mod except the removed ones, `enabled` = currently active, sorted by casefolded
name. That is both "save the mods that are on now" and "save an edited set".
Renaming a set to another spelling of its own name is allowed. `import_mod_set`
takes exactly one of `set_file` and `content` and stores the set under `name`
when the exported name is taken; `bind_profile_set` without `set_name` unbinds,
and does not validate the profile id. There is no separate "get the binding of a
profile" function — the bindings come with `list_mod_sets`. The feature has no
CLI commands; it is reached through RPC and the desktop app.

**Plan matching.** Against `list_mods(path, include_all=True)`, linked by
non-empty `forge_id` first and by casefolded name otherwise:

| Set entry | Installation | Plan |
|---|---|---|
| on | active | `unchanged`, plus `different` when the set names another version |
| on | disabled | `enable` |
| on | removed or not installed | `missing` |
| off | active | `disable`, reason "the set turns this mod off" |
| off | anything else | `unchanged` |
| not named | active installed mod | `disable`, reason "the set does not include this mod" |

`different` is report-only. The mods no entry names are appended to `disable`
sorted by casefolded name.

**Applying.** `apply_mod_set` raises `ModSetIncompleteError(name, missing)`
before touching anything when the plan has missing mods and `ignore_missing` is
not set; with the flag the rest of the set is applied and the missing names are
reported in the result. It takes NO lock of its own: it calls the public
`disable_mod` / `enable_mod`, each of which takes the apply lock and runs
`ensure_installable` for itself. Nesting `game_lock` would self-deadlock, since
each call opens the file freshly and `flock` is per open file description. All
disables run before all enables. A mod that reported a no-op instead of changing
contributes its `ToggleResult.notice` to `notices` and does not appear in
`enabled`/`disabled`. Errors (`ConflictError`, `DirtyTreeError`,
`GameRunningError`, `OperationInProgressError`, `GitError`) propagate at once —
the changes made up to that point stay applied. Progress: `plan` at 0 "Working
out what to change", then per change `int(100 * done / total)` with
`Turning off <mod>` / `Turning on <mod>`, then `done` at 100 "The mod set is
applied"; a set with nothing to change reports only `plan` and `done`.

**Errors and RPC codes:** `ModSetNotFoundError` (`MOD_SET_NOT_FOUND`),
`ModSetExistsError` (`MOD_SET_EXISTS`), `ModSetIncompleteError`
(`MOD_SET_INCOMPLETE`, `details.name` and `details.missing`, the latter an
additive `list[str]` field of `schemas.ErrorDetails`). Unreadable stores,
unusable import sources and empty names reuse `InvalidMetadataError`.
`SaveModSetRequest.service_kwargs()` overrides the `CoreModel` hook to hand the
service its core `ModSetEntry` objects instead of the schema mirrors.

**Frontend.** `src/lib/modsets.ts` holds the pure logic (entry rows with their
`on|off|missing|version` status, forge-id-first matching, set options, plan
summary, export file name sanitising, `missingFromError`, install targets that
resolve a missing mod's Forge id from the bound set so the install call uses the
id rather than the trailer name). `ModSetEditor` sits in every readable profile
panel of the Profiles page: a native select of the sets plus "No set", the entry
table with checkboxes and status badges, "Save" and "Save the installed mods as
this set", and the line "Starting this profile: …" from `plan_mod_set`. The
Mods view has a "Save current mods as set" button with a name prompt; a name
that already exists is confirmed before the set is replaced. The Settings page
has a "Mod sets" section listing the sets with Rename / Export (native save
dialog) / Copy share code (clipboard) / Delete, and an "Add a set" block taking
a picked file path or a pasted code — a pasted code wins over the file path, and
`MOD_SET_EXISTS` asks for another name. `ModSetDialog` is the "Mods are missing"
dialog with "Install the missing mods" and "Play without them".

## Fika co-op

Fika lets several players share one SPT server: one machine runs it, the others
point their game at that machine. Which of the two a game folder is — `none`,
`host` or `client` — is a setting of that folder. A `host` folder is an ordinary
installation and launches through the local-server path; hosting means only that
the other players connect to the server it starts. A `client` folder starts no
server: the profile it plays, the sign-in and the mods all live on the host.

**Storage.** `platformdirs.user_config_path("sptmod", appauthor=False) /
"fika.json"` (`core/fika.py`), one document for the user, keyed by
`str(game_dir.resolve())` — the key the mod sets use (`modsets.store_key`), so
several game folders keep their own role. It is a file of its own rather than a
section of `modsets.json` because the two stores answer damage differently: a
mod set decides which mods a player runs, so an unreadable store fails loudly,
while an unreadable Fika store reads as "this folder plays alone" (the tolerant
`favorites.py` pattern) — a damaged setting must never stand between a player
and their game. It is deliberately not a file in the game repository, for the
same reasons as the mod sets: it would need whitelist changes, would surface in
the `game_files_changed` bucket with its SPT-version side effect, and would
compete with git as the source of truth about the installation. The price of the
tolerant read is the failure mode: a lost role degrades to a local launch, which
the player sees at once (the game plays alone) and repairs in the settings.
Writes rewrite the whole document through mkstemp + `os.replace`, keeping every
other game folder's entry, so a crash mid-write leaves the previous document
intact. Every service function takes a keyword-only `fika_path` seam, so no test
writes the real file.

On-disk shape:

```json
{"version": 1, "games": {"/home/user/Games/SPT": {"role": "client", "host_address": "192.168.1.20"}}}
```

Only the typed address is stored. `FikaSettings.base_url` — the address the game
is pointed at — is derived on every read, so an address that stopped being usable
keeps its text and loses its base URL, and the launch says what is wrong instead
of silently playing solo. A role the file does not name, or names unknown, reads
as `none`.

**The canonical address.** `canonical_base_url` (`core/fika.py`) and
`canonicalBaseUrl` (`frontend/src/lib/fika.ts`) implement one rule against the
same example table in both test suites: whitespace around the address is
dropped; an empty address stays empty (that is what "no host" looks like); a
scheme is kept when it is `http` or `https` in any casing and assumed `https`
otherwise; the address is refused when the host is empty, when user info is
present, when a `?` or a `#` appears anywhere (including a trailing one that
would open an empty query or fragment), when brackets hold anything but an IPv6
literal, when the path is anything but `""` or `/`, when the port is outside
1–65535, or when there is whitespace inside; the result is
`scheme://host:port` with a lower-cased host, the brackets of an IPv6 address
kept, and port `6969` when none was given.

| Typed | Base URL |
|---|---|
| *(empty)* | *(empty)* — no host set |
| `192.168.1.20` | `https://192.168.1.20:6969` |
| `192.168.1.20:7000` | `https://192.168.1.20:7000` |
| `http://pc.local` | `http://pc.local:6969` |
| `https://[fe80::1]:6969/` | `https://[fe80::1]:6969` |
| `ftp://pc.local`, `a b`, `:6969`, `pc.local:0` | refused |
| `pc.local?`, `pc.local#`, `[abc]`, `[1.2.3.4]:7000` | refused |

Python raises `InvalidMetadataError` for a refused address, the TypeScript twin
returns `null`; an empty address is `""` on both sides.

**The host's profiles.** `sptserver.host_profiles(base_url)` reads GET
`/launcher/v2/profiles` and maps the `Response` list into `RemoteProfile`
(`profileId`, `username`, `nickname` falling back to the username, `currlvl`,
`side`, `hasPassword`); an entry that is not an object or carries no profile id
is left out, and `httpx.HTTPError` becomes `LaunchError`. A client machine has
no profile files of its own — the `-token=` id belongs to the host's server — so
the list a player picks from has to come over the network. Certificate
validation stays off, exactly as for the local server: every SPT installation
generates its own self-signed certificate, the host's as much as the local one,
and the address is one the player typed deliberately.

**Service functions** (`services/fika.py`, all with keyword-only `fika_path`,
except `plan_fika_install`, which lives in `services/install_forge.py` and takes
`db_path`):

| Function | Purpose | Errors |
|---|---|---|
| `get_fika_settings(path)` | role, typed address and derived base URL of the folder | — (reads only, never fails) |
| `set_fika_settings(path, role="none", host_address="")` | store the role and the address, returning what was stored; role `none` stores an empty address without checking what was passed | `InvalidMetadataError` (address unusable, or `client` without one), `OSError` |
| `list_host_profiles(path)` | the profiles of the host, in the order it sends them | `FikaHostUnreachableError` (no usable address stored, or the host does not answer) |
| `plan_fika_install(path)` | the two Forge ids of Fika and one plan installing both | `NoCompatibleVersionError`, `UnresolvableDependencyError`, `NotAGameDirError`, `ForgeUnavailableError`, `ForgeApiError`, `GitError` |

The address is checked in `set_fika_settings`, while the player is looking at the
input, not when they want to play — except for the role `none`, which drops the
address unchecked so a folder can always go back to playing alone. The role is validated by pydantic at the RPC
boundary, so the service does not check it again.

**Installing Fika.** Fika is published as two Forge mods and both are found by
slug, never by a hardcoded id: `project-fika-server` ("Project Fika - Server",
the server mod) and `project-fika` ("Project Fika", the client plugin).
`catalog.find_by_slug` matches the slug casefolded — the slug is the stable name
in a mod's Forge address where the display name is not. Each half is resolved on
its own through the existing plan resolution (highest version compatible with the
installation's SPT version, full dependency resolution), because the halves carry
their own versions and their own dependencies; the two entry lists become one
plan, the server half first, every mod id appearing once, under the server plan's
`spt_version`. A slug the catalog does not know raises
`NoCompatibleVersionError("<title> is not in the mod catalog. Sync the catalog
and try again.")`. Installing is not a service of its own: the Settings page
confirms the combined plan once and then calls `install_mod` twice, the server id
first and the client id second, both under one `operation_id` so the progress
line runs through.

**Playing the host's mods.** The share code of a mod set carries no host address:
a set describes mods, the address belongs to the joining machine, so one code
serves every player and survives a host that moves. The joining player pastes the
code (or picks the file) in Settings, which calls `import_mod_set` and then
`apply_mod_set` on the imported name; `MOD_SET_EXISTS` asks for another name and
`MOD_SET_INCOMPLETE` opens the existing `ModSetDialog`, whose answers install the
missing mods by the Forge ids the set carries or apply the rest with
`ignore_missing`.

**Errors.** One new exception: `FikaHostUnreachableError`
(`FIKA_HOST_UNREACHABLE`, no `ErrorDetails` field) for a host that does not
answer as an SPT server and for a folder that joins a host without a usable
address. The rest reuses what exists: `LoginFailedError` (the host refuses the
profile — a password-protected profile cannot be played from another machine),
`LaunchError` (the host offers no profile with that id; no usable address at
launch time), `InvalidMetadataError` (the address is refused when it is saved),
`NoCompatibleVersionError` (a Fika half is missing from the catalog),
`ModSetExistsError` / `ModSetIncompleteError` (the shared set).

**Frontend.** `src/lib/fika.ts` holds the pure part: the three role options with
their labels and hints, `canonicalBaseUrl`, `hostLabel` (the host without the
default port: `pc.local`, or `pc.local:7000`) and `addressError` (the line under
the input, empty when the role can be saved). The Settings page has a "Fika"
section between Launching and Mod sets: three radios (No Fika / I host / I join a
host), the address input for `host` and `client` with its live error, Save, the
"Install Fika (server mod + client plugin)" button with the plan dialog, and the
"Apply the host's mod set" block (file path plus paste field, the pasted code
wins). The Profiles page reads `get_fika_settings` on mount; in the `client` role
it lists the host's profiles instead of the local ones — nickname, level, side,
a `ModSetEditor` and a Connect button, whose label comes from `launchAction(…,
hostLabel(base_url))` as `Connect to <host>` / "Connecting…". A password-locked
profile disables that button and carries the note that it cannot be used from
here as its hint. With a host label the stop hint becomes "Closes the game. The
host's server keeps running.", and the closing dialog says the same instead of
naming a server that is closed. Profile residues
and cleanup are work on local profile files and stay hidden there; a failed
profile list is one line with a "Try again" button. The `none` and `host` roles
render the local profile panels. The Mods view is untouched, including its "Fika
compatible" filter chip, which reports a mod's own compatibility flag and has
nothing to do with this setting.

**Unverified.** Client mode assumes the game needs nothing but the host's
`BackendUrl` in its `-config=` argument, which is what the official launcher
builds — this has not been checked against a live Fika session.

## Launching the game

All three game-launch service functions (`launch_profile`, `launch_status`,
`stop_game`) delegate to `core/launch/` and `core/sptserver.py`. The sequence
is: apply the mod set the profile is bound to → port probe via GET
`/launcher/v2/ping` → start `SPT.Server.*` if the port is free → poll readiness every 1 second with a 120-second bound → PUT
`/launcher/v2/login` (zlib-compressed body, `responsecompressed: 0` header) →
spawn the game client with the exact argv, cwd and environment from the official
launcher's logic → spawn a detached watcher that polls the process table every
3 seconds and closes the server when the game closes (if configured).

**Mod set:** `launch_profile(path, profile_id, stop_server_on_exit=True,
mod_set="apply", progress=None, *, store_path=None, fika_path=None)`. The `mod_set` parameter
decides what happens with the set the profile is bound to: `apply` refuses to
launch when the set names mods the installation does not have, `ignore_missing`
applies the rest of the set anyway, `skip` does not read the stored sets at all
and leaves the mods untouched. A profile without a binding is played with the
mods as they are and starts at the server phase. Otherwise nothing is started
before the mods are the set's: progress reports `modset` at 0 % with
`Applying the mod set for <nickname>` (the nickname from `list_profiles`,
the profile id when it has none), then the apply's own steps forwarded as
`modset` scaled into 0–4 % (`percent * 4 // 100`) so the server phase still
begins at 5 %. The apply's closing step is dropped, because its name is the name
of the launch's own final step and would look like a game that is ready to play.
Missing mods fail the launch with `ModSetIncompleteError`
(`MOD_SET_INCOMPLETE`, `details.name`, `details.missing`) before any process is
started; the caller installs the mods through the usual Forge flow or relaunches
with `ignore_missing`. A damaged mod set store fails every launch with
`InvalidMetadataError` — only `mod_set="skip"` starts anyway. `LaunchStep` gained
`MODSET = "modset"` as its first phase.

**Fika client mode:** after the mod set step the service reads the Fika settings
of the game folder. Role `client` hands the host's base URL to the core function
as its keyword-only `fika_host`; role `client` without a usable address fails with
`LaunchError("No usable address is set for the Fika host. Set it in the
settings.")` before anything starts; the roles `none` and `host` pass an empty
`fika_host` and take the local-server path above. With a `fika_host` the only
thing that starts on this machine is the game:

| Step | % | Message |
|---|---|---|
| `host` | 5 | `Contacting the host at <base URL>` |
| `host` | 40 | `Reading the profiles on the host` |
| `login` | 80 | `Signing in as <nickname>` |
| `client` | 95 | `Starting the game` |
| `done` | 100 | `The game is starting` |

The host is probed first (`GET /launcher/v2/ping`); anything but an SPT answer
raises `FikaHostUnreachableError` with the reason spelled out — nobody answering
("Ask the host to start their server, then try again."), no answer in time, or
something that is not an SPT server. Then the host's profile list is read and the
entry whose id is the launched one supplies the username and the player name; no
local profile file is opened, and an id the host does not offer is a `LaunchError`
naming it. The sign-in goes to the host, and the game is spawned by the same code
as a local launch with the host's URL as its `BackendUrl`, so argv, working
directory and environment are identical. `LaunchStep` carries `HOST = "host"` for
the two host phases; the result carries `server_pid=0` and
`server_started=False`, within the existing `LaunchResult`. The launch log gets
`Connecting to the host at <url>.` and `The host signed <nickname> in.`

**State file:** `<game dir>/.sptmod-launch.json` holds the running process
identities (server, client, watcher) along with `profile_id`,
`stop_server_on_exit`, `failure` (empty unless the watcher gave up waiting
for the game) and `mode` (`client` when the game was started on another player's
server, empty otherwise; a file without the key reads as empty, so a state file
from an older run stays usable). `mode` is what keeps a client-mode stop off the
host's server: nothing about it is remembered, so `stop_game` has no server to
close and `server_stopped` is always false while the game is closed the same
way, and the watcher logs "The game was played on another player's server;
nothing to close here." instead of closing anything. `launch_status` reads `mode` the same way: in client mode it
reports no server at all, so a server running on the client machine for another
reason does not look like the one the game plays on. The file is read and written atomically (mkstemp + os.replace,
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
`stop_game` closes only what the launch state file recorded, identified by pid
and start time together, and the server only when `server_started_by_sptmod`
says sptmod started it rather than adopted one that was already answering. A
process the scan alone found is reported (`launch_status` sets `server_running`
/ `client_running` from it) but never signalled, and the `server_stoppable` /
`client_stoppable` flags say which of the two sptmod may close. Because the
recorded client on Linux is only the wrapper, the game frequently outlives it:
`stop_game` therefore looks at the scan once more afterwards, purely to say in
its `message` that the game is still running and has to be closed by hand.

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
`GameRunningError` (`GAME_RUNNING`). A launch on a Fika host adds
`FikaHostUnreachableError` (`FIKA_HOST_UNREACHABLE`), see "Fika co-op".

**Frontend:** Three new routes in the Profiles tab (Launch/Stop buttons per
readable profile with phase-progress display) and the Mods view (running-game
banner, mod-row lock while game is up). A launch refused with
`MOD_SET_INCOMPLETE` opens `ModSetDialog`, whose two answers install the named
mods or relaunch with `mod_set: 'ignore_missing'`. Settings page gains a "Launching" section with
one checkbox: "Stop the server when the game closes" (persisted in localStorage,
default true, passed as a parameter to each launch call).

## Updating SPT

**Release source.** SPT's release hosting is split across two GitHub repositories, and
`core/sptrelease/client.py` reads both: `SP-Tushonka/build` carries the current line (4.1.3 and newer),
`sp-tarkov/build` the older half (up to 4.1.2, published 2026-08-06, from an org that is archived but still serves
its releases and assets). sp-mod.com's own `/api/v0/spt/versions` splits the same way. Each source is read
anonymously with one GET of 100 entries (that covers every release a source ever published, so pagination is never
followed), so a listing costs two of the 60 requests per hour per IP. The results are merged de-duplicated by
version — the first source listed wins a collision, because the current repository is authoritative for any version
it republishes, even when the archived entry for that version is the one carrying an asset — and sorted by
publication date, newest first. A failure of
*any* source raises `SptReleaseUnavailableError`, so a short but plausible-looking list can never reach the caller.
`SPTMOD_SPT_RELEASE_URL` replaces both defaults with one endpoint for mirrors and tests. Drafts, pre-releases and
entries without a readable `x.y.z` tag are dropped; a leading `v` on old tags is stripped. The required Escape from
Tarkov build is parsed from the release name (`SPT 4.1.2 (40743)`); when a name carries no build,
`<runtime>/SPT_Data/configs/core.json` of the unpacked release answers with the trailing component of
`compatibleTarkovVersion` (`0.16.9.40743` → `40743`). Release bodies are never parsed — their wording is
inconsistent across versions. Two sources were rejected. The Forge's `/spt/versions` does list every 4.x in one
page, but it mirrors the release metadata rather than hosting the releases; the download URLs, sizes and publication
dates come from the GitHub release APIs, so those are read directly. The installer feed `release.json` describes only
the newest release.

**Almost no release keeps its asset** — an asset survives only on the newest patch of a line at the time its
repository stopped publishing that line (verified: 4 of the 86 merged releases have one; the 4.1 line has two,
4.1.2 from `sp-tarkov/build` and 4.1.5 from `SP-Tushonka/build`, plus 4.0.13 and 3.11.4; the mirror URLs in older
release bodies answer 404). `list_spt_releases` therefore reports every release of the installed series with a
`downloadable` flag, and the history undo — not a download — is the primary downgrade path. The UI and the CLI say
so instead of promising more.

**Applying a release** (`services.apply_spt_release`, one function for update and downgrade):

1. `detect_layout`, `Repo`, `ensure_installable` — the usual auto-snapshot of profiles, mod settings and changed game
   files, so there is a state to return to.
2. Resolve the version against the release list. Unknown version, a version of another SPT series than the installed
   one (a series change means the other runtime directory, which only the official installer does) and a version
   without a published download all raise `SptReleaseUnavailableError` pointing at `https://sp-mod.com/installer`.
3. Download through the existing cache (`forge/download.download_cached`) with the key `spt-<version>`; the key is
   non-numeric, so it cannot collide with the numeric Forge version ids. Size verification is the cache's usual one.
4. Extract into the staging area. py7zr stays the primary backend; SPT release archives are LZMA2 + BCJ2, which py7zr
   does not implement, so `archive._extract_7z` falls back to libarchive-c on
   `UnsupportedCompressionMethodError`. Member names are validated in a first read pass before extraction, as for
   every other format. Without the system library the fallback raises `ArchiveError` carrying
   `LIBARCHIVE_INSTALL_HINT`, in the same shape as the existing rar backend hint.
5. **Client-build check, before anything is written.** The build the release needs is compared with the
   `ProductVersion` of `EscapeFromTarkov.exe` (`game_dir.read_client_build`: `-` counts as a separator like `.`, the
   second-to-last component is the build, `FileVersion` is read when there is no `ProductVersion`). A mismatch *and*
   an unreadable installed build both raise `ClientBuildMismatchError` naming the official installer — a deviation
   from the design's proposal to warn and proceed, because writing a program set the game cannot load is the worse
   outcome. Only a release that names no build and ships no readable `core.json` skips the comparison.
6. Under the game lock: `ensure_installable` again, then the tracked program files the release no longer ships are
   deleted, the include set is copied in with the release's `SPT_Runtime/` prefix rewritten to the layout's runtime
   directory, empty directories are pruned, and everything lands in **one** commit `spt: <version>` — no trailers, so
   the touched paths stay unowned by any mod and later changes to them classify as `game_files_changed`. Afterwards
   `sptmod.sptversion` is resolved again from the freshly written `SPT.Server.exe` and stored.
7. A re-apply of the version already in place writes the same bytes, leaves the tree clean and commits nothing: the
   result carries the version with an empty commit and no files.

**Undoing** (`services.revert_spt_update`): under the game lock, the youngest commit whose subject is
`spt: <version>` or `snapshot: game files updated` and that a later `Revert "…"` commit has not already taken back
(so undoing twice reaches the change before instead of the same one again); when there is none, a plain `GitError`
("There is no SPT version change to undo."). Otherwise `ensure_installable`, then one revert and the version stored
again. A revert git cannot apply is reported the same way: "Going back to the previous SPT version did not work:
these files have changed since then: <paths>" — or "…: the game folder no longer matches that state" when git names
no path — with the conflicting paths carried on the error. Installed mods keep their files, because such a commit
never contains any.

**What a version change never touches:** player profiles and everything else under `<runtime>/user/`,
`BepInEx/config/**` (BepInEx and mod settings), and every plugin or patcher that is not SPT's own. These are
structurally outside `is_game_program_path`, so they are neither written nor deleted nor part of the commit; a
release that shipped such a file would have it dropped silently. The four loader files in the game root are matched by
name, case-sensitively, exactly as git applies the whitelist patterns.

**Re-running the compatibility check is the caller's job.** The service reports nothing about mods: the Settings page
invalidates its cached views and points at the Updates tab, the CLI prints the hint to run `sptmod update --check`.

**CLI:** `sptmod spt list` (table Version | EFT build | Released | State | Download, plus the installed EFT build),
`sptmod spt update <version>` and `sptmod spt downgrade [version]`, the latter without an argument being the undo.
Both changing commands confirm first unless `--yes`/`-y` is given, and the confirmation refuses on closed stdin, so
`--yes` is required for non-interactive use. `update` refuses a version that is not newer and `downgrade` one that is
not older (exit 2, typer's parameter error); when the installed version cannot be read there is nothing to compare
against and the direction check is skipped. Exit codes: 18 `SptReleaseUnavailableError`,
19 `ClientBuildMismatchError`, 20 `SptReleaseWriteError`, plus the inherited 3/4/5/10. A refused write of mod
files (install, update, profile cleanup) is `ModFileWriteError` (`MOD_FILE_WRITE_FAILED`, CLI exit 21), which
names the file it stopped at, what the system said, and that the previous files are one snapshot back.

**RPC:** `list_spt_releases`, `apply_spt_release` (declares `operation_id`, reports download/extract/copy progress via
the existing `InstallStep` steps) and `revert_spt_update`. New error codes `SPT_RELEASE_UNAVAILABLE` (503 in prose)
and `CLIENT_BUILD_MISMATCH` (409); "nothing to undo" reuses `GIT_ERROR`.

**UI:** the Settings page gains an "SPT version" section (installed version, installed EFT build, the releases of the
line with their required build, date and state, per row an Update / "Go back to this version" button disabled with a
reason when there is no download, and "Undo the last SPT change"). The confirmation dialog names the safety snapshot
in plain language and warns that mods built for the other version may stop working. The app shell
(`src/routes/+layout.svelte`) shows a dismissible startup banner when the newest release of the line is above the
installed version; the dismissal is stored in `localStorage` under `sptmod.dismissedSptRelease` as the version
string, so a later release shows the banner again. A failing release list keeps the shell quiet — only the Settings
page reports it.

**Known limitations:** the release list is fetched fresh on every use (one request per CLI invocation, one per app
start and one per Settings visit) against a 60/h anonymous budget — there is no cache yet. The libarchive fallback
needs the system library (`libarchive` on Arch, `libarchive13` on Debian/Ubuntu); on Windows it would need a shipped
DLL. The `ProductVersion` layout of a real `EscapeFromTarkov.exe` was derived from the official installer's source,
not read from a live install — the first live run should confirm it.

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
- RPC functions: 50 service functions (declared as `FUNCTIONS: [&str; 50]` in
  rpc.rs); the frontend client.ts has 48 camelCase wrappers (`adopt_mod` and
  `install_from_archive` are RPC-exposed but have no wrapper or frontend caller).
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
- Settings page: sections Game directory, SPT version, Appearance (theme
  select: system, light, dark), Launching, Fika, Mod sets, Export the mod
  list, Import a mod list, Catalog cache (two actions: clear cache alone, or
  clear cache + remove downloaded mod files) and Updates. A left sub-nav links
  the sections by hash; the active entry follows `page.url.hash`. The SPT
  version section lists the releases of the installed line with the required
  Escape from Tarkov build and applies or undoes one (see "Updating SPT"); the
  app shell carries the dismissible startup notice when a newer release exists.

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
  next to its `_internal/`. tauri-build resolves resource paths at compile time
  and a missing `src-tauri/sidecar/` is a hard build error (ResourcePathNotFound),
  so the declaration lives in the overlay `src-tauri/tauri.bundle.conf.json`
  instead of `tauri.conf.json`: a fresh checkout runs `cargo build`, `cargo test`,
  `cargo clippy` and `cargo tauri dev` without the sidecar, and bundle builds merge
  the overlay in with `cargo tauri build -c src-tauri/tauri.bundle.conf.json`
  (`-c` takes a file path as well as inline JSON and may be repeated; later values
  win). Without the overlay `cargo tauri dev` copies no resources, so dev runs
  resolve `sptmod` on PATH — which debug builds prefer anyway.
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
key is absent. `tests/test_version_agreement.py` checks that `sptmod version` prints
the pyproject version; the Cargo and tauri.conf.json conditions are enforced by the
release workflow before it builds.
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
coverage, the component suite with coverage, build), `sidecar` (matrix
Linux/Windows: build the frozen CLI, then
smoke-test `sptmod version`, `rpc list_directories` and an offline
`rpc search_catalog` against a scaffolded game directory, upload as artifact),
`rust` (`cargo build` + `cargo test`, no sidecar needed), `app`
(matrix Linux/Windows, needs sidecar and frontend, full `tauri build`) and
`workflows` (actionlint). `app` downloads the sidecar artifact and restores its
exec bit because upload-artifact drops file permissions; it passes
`-c src-tauri/tauri.bundle.conf.json` to bundle the sidecar and overrides
`bundle.createUpdaterArtifacts` to false, since the CLI aborts without a signing
key. `app` uploads the resulting packages (deb and AppImage on
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
so parallel platforms would drop each other's entries), passes
`-c src-tauri/tauri.bundle.conf.json` through tauri-action's `args` so the sidecar
is bundled, and uploads installers and manifest into a draft release. Two guards run before anything is built: the job
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
  shows the full path and a "Change folder" button that goes to Mods and
  requests the folder change there.
- `CommandPalette` — the Ctrl+K (Cmd+K) overlay the layout always renders:
  one input, a grouped result list (Go to / Actions / Mods) and "No matches".
  Arrow keys move the highlight, Enter runs the entry, Escape closes and
  returns focus where it was; Tab is swallowed, so the input keeps the focus.
  It reads the mod rows from `shellstate`, loads the profiles from the same
  cached `list_profiles` view the Profiles page fills, and turns an entry into
  a route change, an intent, a filter reset or the theme toggle.
- `SubNav` — the left column the layout renders when a page publishes entries;
  a click emits the selection back to that page. Only Profiles publishes
  entries; the mod list relies on `QuickTabs` alone and keeps the store empty.
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
  the sub-nav entries, the search-enabled flag, the game folder path and the
  mod rows the palette offers, plus the sub-nav selection signal. A
  subscription delivers the current value immediately and returns an
  unsubscribe function; every page calls it on destroy and resets what it
  published.
- `intents.ts` — the queued channel for requests that cross a page change:
  sync, update all, change folder, snapshot, launch a profile and reveal a
  mod row. At most one request per kind is pending and a newer one replaces
  it; the page that owns the action drains its kinds on mount and on every
  notification, so a request survives until its page is there.
- `fuzzy.ts` — the subsequence match behind the palette: a score that rewards
  a prefix, a word start and consecutive letters and penalises gaps, plus
  `rank`, which drops the non-matches, orders by score and truncates.
- `commands.ts` — the palette registry built from the current page, game
  folder, theme, profiles and mod rows: the four navigation entries, the
  game actions (sync, update all, snapshot, one launch per profile, change
  folder), clearing the filters, the theme toggle and one entry per mod.
- `palettekeys.ts` — what Ctrl+K/Cmd+K looks like, and the palette key
  handling: arrows wrap through the list, Enter runs, Escape closes, Tab is
  swallowed, plus clamping the highlight when the list shrinks.
- `rownav.ts` — the mod table key handling: the arrows move the focused row
  and stop at the ends, Enter opens the details, Space picks the row, `f`
  toggles the favorite and Escape gives up the focus. The focused row is kept
  as a mod id, so filtering and sorting keep it.
- `filterstate.ts` — the single source for search text, pills and quick tabs.
  It holds one `ModFilter` (chips, category, search, the SPT version the list
  is judged against), defaults to compatible-only against the installed
  version, survives route changes but not an app restart, and notifies
  subscribers only when the value really differs.
- `filterpills.ts` — the pill definitions (Compatibility, Category, Type,
  Fika, State, Featured), which pills are active for a filter, applying and
  removing a pill, and the quick tabs. Every pill is a radio because chips are
  ANDed; the Compatibility pill shows the SPT version as its label when one is
  known. It offers the installed version first, then one option per
  installable release (`installableVersions` over the `list_spt_releases`
  answer, downloadable ones, newest first, the installed one left out), then
  "All versions"; a version option carries that version in the filter, which
  `ModsView` hands to `browse_catalog` so the backend judges the rows, the
  compatibility chip and the counts against it. An unreachable release source
  leaves the pill with the two options it always had — the Mods view never
  raises for it and stays usable offline.
- `popover.ts` — the key handling of a popover: Escape closes, Tab and
  Shift+Tab cycle through the focusable elements of the panel.
- `selection.ts` — toggle one row or all, the tri-state header checkbox,
  pruning a selection to the visible rows, the bulk actions a selection offers,
  and `runBulk`, which walks the names one at a time and records failures
  instead of aborting.

### Mods view behaviour

- `ModsView` mirrors `filterstate` into local state and writes changes back,
  publishes the visible rows for the command palette, and enables the top-bar
  search while it is mounted. It drives the tabs through `QuickTabs` alone and
  keeps the sub-nav store empty, clearing it on mount and on teardown, so a
  column published by a page visited earlier cannot leak onto the mod list.
- A filter change prunes the selection to the rows that stay visible, so a bulk
  action can never touch a mod the user cannot see.
- Bulk actions reuse the per-row RPCs `enable_mod`, `disable_mod` and
  `update_mod` sequentially; there is no multi-row RPC. Two mod operations at
  once would queue on the backend lock for minutes. Failures are collected and
  shown as one line above the table.
- `ModTable` owns the focused row, keeps it valid when the rows change and
  focuses it when the palette reveals a mod; `ModRow` carries the focus ring
  and applies the key handling of `rownav.ts` to its own actions.
- `ModTable` is a container-query container: below 1060px table width the
  Downloads, Updated and Type columns drop out of the header and the rows, and
  the shared `--mods-grid` template shrinks with them.
- `StatusReport` carries an additive `spt_version` field (empty string when
  unknown), filled locally by `resolve_spt_version`. The Mods page takes it
  from the status call it already makes and hands it to the Compatibility pill,
  which then reads "SPT <version>" instead of "Compatible".

**Deferred** (no data or demand for them yet): shadcn-svelte / Tailwind /
TanStack Table, loading skeletons, detail side panel (the detail dialog
covers it), pagination.

## Testing

**The pytest suite is the Rust-port specification** — tests are black-box,
behavior-focused; do not weaken them to make an implementation fit.

### Quality gates

```bash
uv run pytest                        # Python suite
uv run pytest --cov                  # with coverage gates (line & branch 100%)
uv run pyright                       # strict type checking on src + tests
uv run pylint src tests              # strict linting (design limits to observed maxima)
uv run mypy --strict src tests       # strict typing on both src and tests
cd frontend && pnpm vitest run --coverage  # src/lib (gated at 100% coverage)
cd frontend && pnpm test:components --coverage  # components (measured coverage floor)
cargo build && cargo test            # Rust (driver tests plus ignored e2e)
cd src-tauri && cargo llvm-cov       # Rust coverage - reported only, no enforced floor
```

### Suite sizes and coverage

- **Python:** 2137 passed, 1 skipped (measured 2026-09-09; nothing enforces the
  count, so re-measure rather than trusting it); `uv run pytest --cov` enforces 100%
  coverage (line and branch) on `src/sptmod` (coverage config in pyproject.toml).
  Run `uv run pytest` without --cov for development (no speed penalty, coverage
  excluded from default pytest addopts so single-file runs stay usable).
- **Frontend (`src/lib`):** 789 vitest tests in 26 files, run by `pnpm test` in a
  `node` environment (`vite.config.ts`); strict 100%
  statements/branches/functions/lines on `src/lib/**/*.ts` (minus `*.test.ts` and
  generated `api/types.ts`; v8 provider).
- **Frontend (components):** 264 vitest tests in 10 files under `frontend/tests/`,
  run by `pnpm test:components` against `vitest.components.config.ts` (happy-dom,
  `@testing-library/svelte`). Its thresholds over `src/routes/**/*.svelte` and
  `src/lib/components/**/*.svelte` are the measured floor of what these tests
  reach — statements 94.19, branches 76.68, functions 93.91, lines 93.17 — not a
  100% gate: raise them together with new tests, never round them up.
- **Rust (Tauri):** 15 driver tests + 2 ignored e2e tests. No coverage floor is
  enforced anywhere, so `cargo llvm-cov` reports a number nothing verifies. rpc.rs's
  `#[tauri::command] rpc()` glue is covered only by the `#[ignore]` e2e test,
  which needs Wry display; the rest of the uncovered lines in `cargo llvm-cov`
  are async resume points it cannot attribute plus the error path in
  `child.wait()`.
- The component suite mocks `src/lib/api/transport.ts` and nothing else, so the
  real client layer runs and every test pins the RPC name and payload the
  component actually sends. happy-dom rather than jsdom, because jsdom provides
  neither `matchMedia` nor `scrollIntoView`, both of which shipped components
  call. Known gap: `<select bind:value>` cannot be driven under happy-dom (Svelte
  reads the selection via `querySelector(':checked')`, which happy-dom answers
  with null), so the JSON branch of the settings export stays untested.

### Test details and fixtures

- Fixtures in `tests/conftest.py`: `fake_game_dir` (4.0 `SPT/` layout — the
  bulk of the suite), `fake_game_dir_runtime` (4.1 `SPT_Runtime/`), `clean_repo`,
  `install_commit`, `make_archive` (zip/7z; rar cannot be created — real samples
  in `tests/data/`, plus an encrypted zip).
- Fixtures in `tests/fixtures_launch.py`: `fake_launchable_dir` (4.1 layout +
  server/client stub scripts + launcher settings + profile with login
  credentials), `launchable_repo` (`fake_launchable_dir` initialized as a clean
  repository, for mod set launch tests), `mod_set_store` (a `modsets.json` path
  below `tmp_path`, passed as `store_path` so no test writes the real one),
  `fika_store` (the same for `fika.json`, passed as `fika_path`).
- Fixtures in `tests/fixtures_profile.py`: `make_profile` (a realistic profile
  document covering every residue category). Shared builders, imported directly:
  `tests/forge_json.py` (Forge API mock payloads), `tests/github_json.py` (SPT
  release payloads), `tests/pe_builder.py` (PE file assembly),
  `tests/launch_helpers.py` (the respx routes, progress capture and poll helper of
  the launch modules), `tests/forge_install_env.py` (seeded catalogs, dependency
  nodes and download mocks of the Forge install modules) and
  `tests/profile_cleanup_corpus.py` (the content IDs and profile documents of the
  cleanup modules). The area fixture modules `tests/fixtures_forge.py`,
  `tests/fixtures_launch.py`, `tests/fixtures_profile.py`, `tests/fixtures_rpc.py`,
  `tests/fixtures_spt.py` are registered by `tests/conftest.py` through
  `pytest_plugins`. Fixture types: `tests/fixture_types.py` (Protocols for typed
  fixtures).
- Every test module, class, and function has a docstring stating setup, assertion,
  and reason. Tests are fully typed via fixture Protocols; NO suppression pragmas
  in src or tests (`type: ignore`, `noqa`, `pylint: disable`, `pragma: no cover`).
  Lint findings are resolved by refactoring; the only configured knobs are pylint
  design limits set to the exact observed maxima — among them
  `max-module-lines = 900`, set by `tests/test_services_update.py`, the longest
  module in the tree —
  `min-similarity-lines = 24` for the CatalogMod dataclass/schema mirror, and
  `[tool.pylint.imports] allow-any-import-level = ["libarchive"]`, because
  importing the bindings loads `libarchive.so` and the import therefore has to
  stay inside the 7z fallback.
- All Forge tests run against respx mocks shaped by docs/FORGE_API.md; the
  suite NEVER hits the network. Live verification happens manually against
  sp-mod.com (full pipeline verified 2026-09-02: sync 1862 mods, SAIN install
  with dependency commits, update flow).
- Launch tests: `tests/test_sptserver.py` (respx mocks: probe states, login
  true/false, zlib decode fallback), `tests/test_launch.py` (everything
  `launch_profile` refuses before starting anything, plus
  `tokenize_launch_settings`), `tests/test_launch_client.py` (black-box: the happy
  path, exact client argv/cwd/env, state file, launch log, launch lock),
  `tests/test_launch_status.py` (remembered state against the process table),
  `tests/test_launch_stop.py` (what `stop_game` closes and what it leaves alone),
  `tests/test_launch_watch.py` (watcher: client pid poll, server lifecycle,
  `started_by_sptmod` flag).
- Mod set tests: `tests/test_modsets_store.py` (the store file itself),
  `tests/test_modsets_plan.py` (the matching table), `tests/test_modsets_apply.py`
  (order, notices, propagated errors, progress), `tests/test_modsets_transfer.py`
  (export content, share code, import sources), `tests/test_launch_modset.py`
  (the bound set in a launch), `tests/test_rpc_modsets.py` (one round-trip per
  function and the three error envelopes). Frontend: `src/lib/modsets.test.ts`.
- Fika tests: `tests/test_fika_store.py` (the store file and the address rule),
  `tests/test_services_fika.py` (the three settings services against a respx
  host), `tests/test_services_fika_install.py` (the two slugs, the merged plan),
  `tests/test_launch_fika.py` (client-mode launch, progress, state file,
  `stop_game`), `tests/test_rpc_fika.py` (one round-trip per function plus the
  `FIKA_HOST_UNREACHABLE` and `INVALID_METADATA` envelopes). Frontend:
  `src/lib/fika.test.ts`.
- RPC tests: `tests/test_rpc_contract.py` (route-per-function, schema-parity,
  error-mapping), `tests/test_dump_openapi.py` (contract drift detection),
  `tests/test_rpc.py` (the NDJSON envelope and the error mapping) with the route
  round-trips in `tests/test_rpc_lifecycle.py`, `tests/test_rpc_catalog.py` and
  `tests/test_rpc_forge.py`.
  Rust: `src-tauri/tests/rpc_driver_tests.rs` (stub-script driven),
  `src-tauri/tests/rpc_e2e_ignored.rs` (real CLI, `#[ignore]`).
- SPT release tests: `tests/test_sptrelease_client.py` (respx mocks shaped by
  `tests/github_json.py`: assets present/absent, `v`-prefixed tags, names without a
  build, error statuses, malformed bodies), `tests/test_services_spt_releases.py`,
  `_apply.py`, `_refusals.py` and `_revert.py` (list/apply/undo, include and
  exclude set, stale deletion, build check, re-apply without a commit) and
  `tests/test_cli_spt.py` (`sptmod spt
  list|update|downgrade`, confirmation, direction refusal, exit codes). The BCJ2
  fallback runs against the committed `tests/data/bcj2-mod.7z` and
  `tests/data/bcj2-traversal.7z` — no Python writer can produce that filter.
- Download cache tests: `tests/test_forge_cache.py` (cache layout, claim/keep/release,
  LRU/part sweep, env cap), `tests/test_forge_download_cache.py` (respx mocks:
  hit/miss, resume 206/200, interruption, size mismatch, sweep).
- Guards worth knowing before "simple" changes: route-per-function,
  dataclass↔schema parity, exact whitelist behavior, trailer roundtrips.

## Environment variables

| Variable | Effect |
|---|---|
| `SPTMOD_FORGE_URL` | Forge API base URL (mirrors/testing) |
| `SPTMOD_SPT_RELEASE_URL` | SPT release list endpoint (mirrors/testing) |
| `SPTMOD_CLI` | path to `sptmod` binary (Tauri RPC driver); wins over the bundled sidecar |
| `SPTMOD_DOWNLOAD_CACHE_MAX_GB` | cap for the download cache (default 20, fractional allowed, non-finite/invalid/≤0 falls back) |
| `SPTMOD_PROCESS_SCAN` | `off` switches both scans of the process table for SPT off; inherited by children |
| `SPTMOD_SMOKE_GAME_DIR` | Rust e2e test only; override game directory path for `src-tauri/tests/rpc_e2e_ignored.rs` |
| `XDG_CONFIG_HOME` | relocates the catalog cache (platformdirs) |
| `XDG_CACHE_HOME` | relocates the staging dir and download cache (platformdirs) |

## Known limitations / open points

- Stage 2 (Rust core) not started; the pytest suite is its spec.
- Client-DLL metadata (version/GUID) is not extracted from assemblies (by
  design); `--file` installs without metadata are versionless unless
  `--version` is given (CLI prints a hint).
- `.rar` creation is untestable (extraction works via bsdtar).
- SPT keeps a download only for the newest patch a line reached before its
  repository stopped publishing it (4 of the 86 merged releases carry an asset),
  so downgrading by download is effectively dead — the history undo is the real
  downgrade path, and the UI greys out what cannot be fetched.
- The release list is assembled from two GitHub repositories (`SP-Tushonka/build`
  for 4.1.3 and newer, the archived `sp-tarkov/build` for 4.1.2 and older), and
  it has no cache: every listing spends two requests of the
  60-requests-per-hour anonymous budget.
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
- Fika client mode assumes the game needs nothing but the host's `BackendUrl`;
  not verified against a live Fika session.
