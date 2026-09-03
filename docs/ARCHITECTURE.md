# sptmod — Architecture and Session Context

This document is the authoritative context for anyone (human or AI session)
picking up this project fresh. It describes the ACTUAL state of the code,
including every place where reality forced a deviation from the original
specification. Read order for a new session:

1. This file (current truth).
2. `docs/FORGE_API.md` (verified Forge API contract; supersedes HANDOFF §3a).
3. `docs/HANDOFF.md` (the original German spec — historical intent; where it
   conflicts with this file or FORGE_API.md, those win).

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
    repo.py             THE git layer; the ONLY module allowed to use subprocess
    archive.py          extraction (.7z/.zip/.rar), traversal guard, anchor search
    modmeta.py          package.json parsing, trailer mapping, npm-style semver
    conflicts.py        file/DLL/dependency/SPT-version checks (HANDOFF §6)
    profile.py          profile residue detection from install commits (§7)
    services.py         all user-facing operations as pure functions
    workspace.py        staging dir for downloads/extraction, apply lock
    forge/
      client.py         the ONLY module allowed to use httpx; Forge v0 client
      catalog.py        SQLite cache (config dir), sync/search/classify
      download.py       streaming downloads with size verification
      resolve.py        install plans over /mods/dependencies
  cli.py                typer adapter; exceptions -> exit codes; rich tables
  server/
    rpc.py              hidden CLI command `sptmod rpc <name>` (RPC stdin/stdout)
    schemas.py          Pydantic mirror of the core dataclasses (THE contract)
    errors.py           RPC error mapping and exit codes
    dump_openapi.py     python -m sptmod.server.dump_openapi openapi.json
src-tauri/              Tauri v2 desktop app
  src/
    rpc.rs              #[tauri::command] generic invoke handler
    driver.rs           spawns `sptmod rpc <name>` as child process
  tests/
    rpc_driver_tests.rs   with mocked subprocess
    rpc_e2e_ignored.rs    against real CLI on _data/fakegame/
Cargo.toml              workspace (members: src-tauri)
frontend/               SvelteKit, Svelte 5 runes, adapter-static, no SSR
  src/lib/api/transport.ts   uses Tauri `invoke()` and `listen()` (not fetch)
  src/lib/api/types.ts       GENERATED from openapi.json — never hand-edit
  src/lib/api/client.ts      typed wrappers per RPC function
  src/lib/modfilter.ts       pure tab/search/sort logic (unit-tested)
  src/lib/components/        SetupScreen, ModsView, ModRow, Dialog
  src/routes/                / (setup or mods), /history, /profile, /settings
docs/HANDOFF.md         original spec (German); partially superseded
docs/FORGE_API.md       verified API reference (canonical host sp-mod.com)
openapi.json            committed contract dump; frontend types derive from it
_data/mods/             untracked corpus of 21 real mod archives for local runs
```

## Hard invariants (enforced by review and tests)

- `subprocess` only in `core/repo.py`; raw git, no GitPython.
- `httpx` only in `core/forge/client.py` (download.py drives `client.stream`).
- Core raises typed exceptions only; no print/logging outside cli.py/server/.
- One API contract, four transports: service function ↔ `sptmod rpc <name>`
  ↔ Tauri `#[tauri::command] rpc()` ↔ camelCase wrapper in client.ts. Guarded
  by tests: route-per-function, schema-parity, error-mapping completeness in
  `tests/test_rpc_contract.py`; contract drift detection in `tests/test_dump_openapi.py`.
- Contract change loop: edit schemas → `uv run python -m sptmod.server.dump_openapi
  openapi.json` → `pnpm gen:api` in frontend/ → fix client.ts/test literals.
- Frontend transport: async Tauri `invoke()` and `listen()` (progress events);
  `subscribe()` returns `Promise<unsubscribe>` for future extensibility.
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
operation proceeds (that state is the normal aftermath of playing). Only
unmanaged files (manual mod copies, tampered tracked files) block, with a
plain-language message suggesting adopt. `_ensure_installable` in services.py.
Untracked files an active mod writes inside its own directory (settings a mod
generates while playing) are runtime data: `status` leaves them out of the
report, so they never block and are never snapshotted. `remove` and `disable`
delete them together with the mod, because a leftover file would otherwise
keep the directory alive and resurface as a foreign manual install.

## Parallel operations (the apply lock)

Each RPC call is its own process, so several operations can run at once.
Downloads and extraction are unlocked and happen in a staging directory below
the user cache dir (`core/workspace.py`, swept after a day). Only the final
apply step — conflict check, copy/delete, commit — holds an exclusive lock
(`.sptmod-lock` in the game directory, ignored by the whitelist gitignore).
The file is created once and then stays; exclusivity is an operating-system
lock on it (`fcntl.flock`, `msvcrt.locking` on Windows), which the system
releases when the holding process ends, so a crash leaves nothing behind that
blocks — a lock file nobody holds is simply taken. Its JSON content only names
the running operation for the waiter's message. A colliding apply waits (up to
five minutes, reporting that operation through the progress callback) before
raising `OperationInProgressError` (`OPERATION_IN_PROGRESS`); read-only calls
never lock. `_ensure_installable` runs inside the lock; its pre-download run
is skipped while another apply holds the lock, because that apply's
not-yet-committed files would look like manual installs.

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
  detected runtime dir on copy (`_for_game_layout`).
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
  Schema evolution = `_MOD_COLUMN_MIGRATIONS` ALTER list applied on open;
  the cache is disposable. Cached per mod: thumbnail, teaser, favourites,
  endorsements, category title, license name+link, source links (JSON
  column), fika/featured/ads/AI flags. Delta sync via
  `filter[updated_between]` (one-day overlap, idempotent upserts); a FULL
  sync prunes rows the API no longer returns — but never on an empty
  response. Unicode-correct casefold search; `%`/`_` literal.
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
- `get_mod_details`: cached mod + versions plus the LIVE description
  (HTML stripped to text server-side — never render Forge HTML). Degrades to
  cache-only when the Forge is down.
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

## Desktop app (Tauri) and RPC

- The Tauri v2 desktop app runs `sptmod rpc <name>` as a child process,
  marshaling JSON request/response over stdin/stdout. Exit code 0 = success;
  1 = core exception mapped to RPC error code and details; 2 = request error
  (validation, unknown function). Child binary from env `SPTMOD_CLI` (default:
  `sptmod` on PATH; `uv tool install --editable .`).
- RPC response envelope: `{event:"result",data:T}` or `{event:"error",data:{error,code,details}}`.
  Long ops emit zero or more `{event:"progress",...}` lines (ProgressEvent fields);
  `operation_id` is generated and echoed back. Progress and final result/error lines
  are NDJSON on stdout; stderr carries only free-form diagnostics (tracebacks) and
  is never parsed.
- Rust-only error codes: `unknown_function` (not in FUNCTIONS list), `unreachable`
  (binary not found / spawn failed), `bad_output` (no parseable final result line).
- Plugins: dialog (native folder/save/open), fs (write text file), opener (URLs),
  clipboard-manager (copy). Capabilities in `src-tauri/capabilities/default.json`.
- Setup flow is ONE step (path — no login). Folder picking: native OS dialog
  via Tauri plugin → name+fingerprint → `locate_directory` search; fallback
  `list_directories` browser; text input always works. `ALREADY_INITIALIZED`
  proceeds straight in.
- Mods view: three tabs + search + compatible-only (default ON, filter logic
  pure in modfilter.ts), sortable column headers, thumbnails/teaser, install
  plan confirmation (mod + deps + sizes), RPC progress via Tauri event listener,
  force-retry dialogs rendering conflicts/residues/partial installs. Rows linked
  to installed mods BY FORGE ID first (trailer name ≠ catalog display name!),
  name fallback; all actions use the installed trailer name.
- Detail dialog (click the mod name): stats, category/flag tags, license and
  source links, dates, description, version table.

## Testing

- Python: `uv run pytest` (~740 tests), `uv run ruff check src`, `uv run mypy --strict src`.
- Rust: `cargo build && cargo test` from repo root.
- Frontend: `pnpm check` / `pnpm test` / `pnpm build` from `frontend/`.
- Fixtures in tests/conftest.py: `fake_game_dir` (4.0 `SPT/` layout — the
  bulk of the suite), `fake_game_dir_runtime` (4.1 `SPT_Runtime/`),
  `clean_repo`, `install_commit`, `make_archive` (zip/7z; rar cannot be
  created — real samples in `tests/data/`, plus an encrypted zip).
- All Forge tests run against respx mocks shaped by docs/FORGE_API.md; the
  suite NEVER hits the network. Live verification happens manually against
  sp-mod.com (full pipeline verified 2026-09-02: sync 1862 mods, SAIN install
  with dependency commits, update flow).
- RPC tests: `tests/test_rpc_contract.py` (route-per-function, schema-parity,
  error-mapping), `tests/test_dump_openapi.py` (contract drift detection).
  Rust: `src-tauri/tests/rpc_driver_tests.rs` (stub-script driven),
  `src-tauri/tests/rpc_e2e_ignored.rs` (real CLI, `#[ignore]`).
- Guards worth knowing before "simple" changes: route-per-function,
  dataclass↔schema parity, exact whitelist behavior, trailer roundtrips.

## Environment variables

| Variable | Effect |
|---|---|
| `SPTMOD_FORGE_URL` | Forge API base URL (mirrors/testing) |
| `SPTMOD_CLI` | path to `sptmod` binary (Tauri RPC driver) |
| `XDG_CONFIG_HOME` | relocates the catalog cache (platformdirs) |
| `XDG_CACHE_HOME` | relocates the staging dir for downloads (platformdirs) |

## Known limitations / open points

- Stage 2 (Rust core) not started; the pytest suite is its spec.
- Client-DLL metadata (version/GUID) is not extracted from assemblies (by
  design); `--file` installs without metadata are versionless unless
  `--version` is given (CLI prints a hint).
- `.rar` creation is untestable (extraction works via bsdtar).
- sp-mod.com deviates from the old forge source in category/license/
  dependency-embed shapes — the client parses the LIVE shapes (with
  tolerant fallbacks); resolve uses `/mods/dependencies`, which matches.
- Windows `CREATE_NO_WINDOW` process handling is a follow-up; no read timeout
  on child stdout. Linux-only verified (Windows build untested).
