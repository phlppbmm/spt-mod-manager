# sptmod — git-based Mod Manager for Single Player Tarkov

Specification (current, English). Developed by Philipp Baumm (Linux,
EndeavourOS; game via Wine/Proton/UMU-Launcher); targets Linux and Windows.
Code uses `pathlib`; no Linux-only assumptions in the core.

## 1. Context

Single Player Tarkov (SPT, currently 4.1.x) lacks a mod manager that versions
player profiles, reversibly installs mods, and runs on Linux. Existing managers
(SP-EFT Manager, TioEmirs SPT Mod Manager) are Windows-only Electron apps,
track mods via private registries, and ignore profiles. Hand-installed mods
leave residue in profile JSON when removed (custom items, traders, quests)
that crashes the server.

**Core idea:** Turn the SPT game directory into a git repository with a
whitelist `.gitignore` that tracks only mod paths and profiles. Each mod is
one commit with structured trailers. Remove a mod = revert, disable = revert,
re-enable = cherry-pick. The git history IS the mod list. Player profiles
version together with mods, so rollback is atomic. Git is the only source of
truth — no separate registry.

**Positioning:** "The only SPT mod manager that can undo any install, backs up
your profile, runs on Linux and Windows, and whose mod list is a text file
you can paste in Discord."

**User experience:** Two prompts only — game directory path and Forge login
(optional). Then only mod lists (All / Installed / Available) and actions.
No archives, no drag-and-drop, no file dialogs in normal operation. The CLI
stays as a first-class interface for Linux users and SPT dedicated-server
operators.

## 2. Two Stages, One Architecture

**Stage 1 (current):** Python. Core package with pure business logic; two
thin adapters: CLI (typer) and Tauri desktop app (RPC via stdio).
Svelte 5 frontend with 100% test coverage. Goal: complete black-box test
suite against the core as the specification for the Rust port.

**Stage 2 (planned):** Core and CLI ported to Rust. Svelte frontend
unchanged, hosted by Tauri. Tauri RPC replaces stdio. Tests are the spec —
do not weaken them to fit the implementation.

To keep Stage 2 a swap and not a rewrite, three rules:

1. **The core knows neither CLI nor HTTP.** Service functions take simple
   types and dataclasses, return dataclasses, raise typed exceptions only.
   No print, no request object, no logging output except via injected
   callback (progress).

2. **One contract, four transports.** Service function ↔ `sptmod rpc <name>`
   subprocess ↔ Tauri `#[tauri::command] rpc()` ↔ camelCase wrapper in
   `client.ts`. Pydantic schemas ARE the contract: OpenAPI docs
   auto-generated; frontend types derived by `openapi-typescript`; Rust
   port implements the same schema verbatim.

3. **Frontend transport layer.** Single module `src/lib/api/transport.ts`:
   `call<T>(name, payload): Promise<T>` and `subscribe(operation_id,
   handler): Promise<unsubscribe>`. Stage 1: Tauri `invoke()` and
   `listen()`. No other module invokes Tauri directly. Progress events
   (NDJSON on RPC stdout) map to Tauri event stream; frontend never
   polls.

## 3. SPT Facts the Core Must Know

An SPT 4.x mod archive (`.7z`, `.zip`, occasionally `.rar`) contains one or
both of these trees, merged into the game directory:

- **Server mod:** `<runtime_dir>/user/mods/<ModFolder>/`. Always contains
  `package.json` with at least `name`, `version`, `sptVersion` (SemVer
  range), often `author`, `dependencies`. Optional `db/` subtree with
  custom Items, Traders, Quests as JSON.

- **Client mod:** `BepInEx/plugins/<ModName>.dll` or
  `BepInEx/plugins/<ModFolder>/`. .NET assembly metadata (name, version,
  GUID) exists but is not extracted (assembly parsing deferred). Name
  from filename; `--name`/`--version` override on install. Client mods
  write config at runtime to `BepInEx/config/<author.modname>.cfg`.

Archive structure varies widely in practice (wrapper folder, missing `SPT/`
prefix, missing `BepInEx/`, case sensitivity) — a leading support case for
all existing managers. The tool must **search for the anchor point** in the
archive, not assume a layout. Load order is obsolete; SPT 4.x mods declare
dependencies themselves (§6).

**Game directory structure:**
- 4.1+: `SPT_Runtime/` is the server root; `SPT_Runtime/user/mods/`,
  `SPT_Runtime/user/profiles/`.
- 4.0.x: `SPT/` is the server root; `SPT/user/mods/`, `SPT/user/profiles/`.
- 3.x (flat layout): rejected with a clear error.
- `BepInEx/` is layout-independent, always in the game root.

**Profiles:** `<runtime_dir>/user/profiles/<profileId>.json`. The server
creates backups under `profiles/backups/` (not tracked). Residue-relevant
fields: `characters.pmc.Inventory.items[]._tpl` (item-template IDs,
24-hex strings), `characters.pmc.TradersInfo` (keys = trader IDs),
`characters.pmc.Quests[].qid`.

**SPT version:** Resolved via a chain: `core.json` (no `sptVersion` field
in 4.x) → PE version resource of `SPT.Server.exe` (stdlib reader, 8 MB cap)
→ git config key `sptmod.sptversion`. Explicit `--spt-version` on `init`
short-circuits and is stored. Prompted once at setup (before `git init`).

**Process check:** Mods are changed only with game and server closed. Check
processes named `SPT.Server` or `EscapeFromTarkov` (on Linux they appear as
Wine processes ending in `.exe`). Abort with a clear message if running.

**Extraction:** `py7zr` for .7z, `zipfile` for .zip, `rarfile` for .rar
(backend: `unrar` or `bsdtar`, fail with install hint if absent). Validate
all member names for path traversal (`..`, absolute paths). Archives come
from Forge downloads (§4a) or local files (CLI only).

### 3a. Forge API

**Canonical host:** `https://sp-mod.com/api/v0`. The Forge moved there;
`forge.sp-tarkov.com` is defunct. **No authentication exists** — the v0 API
is public read-only. Env override: `SPTMOD_FORGE_URL`.

Relevant endpoints (see `docs/FORGE_API.md` for authoritative details):

- `GET /mods` — catalog (paginated, searchable).
- `GET /mod/{modId}` — detail.
- `GET /mod/{modId}/versions` — versions with `link`, `content_length`,
  SPT compatibility.
- `GET /mods/updates` — version checks for installed mods.
- `GET /mods/dependencies` — dependency resolution.

**Throttle:** Server limit 300/min per IP; client uses 200/min conservatively,
backs off on 429 (honors Retry-After in seconds form). **Cache:** SQLite in
the platformdirs config dir (not in the game dir; it is foreign state).
Full sync on first setup, then delta sync via `filter[updated_between]`
(one-day overlap for idempotence). Disposable cache — if corrupted, delete
the file and re-sync. User can manually trigger sync; also on app startup.

**Download links** are direct (GitHub releases, etc.), no landing pages.
Stream with progress callback; verify size against `content_length`; extract
filename from Content-Disposition header or URL suffix. The user never sees
the archive file.

## 4. Feature Scope

All service functions below are accessible via CLI (`sptmod <command>`) and
RPC (internal only; frontend calls via typed wrappers in `client.ts`).

### 4a. Forge Commands

| Command | Behavior |
|---|---|
| `sync [--full]` | Sync catalog from Forge to local SQLite cache (delta by default; `--full` forces complete resync). Reports new/updated counts. |
| `search QUERY [--compatible]` | Search cache; `--compatible` filters for the detected SPT version. Shows install status from git history. |
| `browse [--installed\|--available\|--all] [--compatible]` | Frontend's three lists as CLI table. "installed" = active mod with `Mod-Forge-Id` or name match; "available" = cached, not installed. |
| `install MOD [--version V] [--force] [--yes]` | **Main path.** Resolves version (default: latest compatible with detected SPT version), queries `/mods/dependencies`, shows install plan (mod + missing deps + sizes), downloads all archives with progress, installs in dependency order. One commit per mod with `Mod-Forge-Id` set. Conflict/profile rules (§6) apply unchanged. With `--yes`, skips confirmation prompts. |
| `install --file ARCHIVE [--name] [--version] [--force] [--yes]` | Local archive install (CLI-only convenience feature, absent from frontend). Attempts Forge match by name/version; sets `Mod-Forge-Id` if unique match found. |
| `update [NAME...] [--check] [--force] [--yes]` | `/mods/updates` check for installed mods; `--check` displays only. Update = download new archive, replace old mod files in single commit (`mod: SAIN 3.2.1 → 3.3.0`), set `Mod-Previous-Version` trailer. Config under `BepInEx/config/` left untouched. |

### 4b. Local Commands

| Command | Behavior |
|---|---|
| `init [PATH] [--spt-version V] [--yes]` | Validate SPT directory (detect layout). Create whitelist `.gitignore`, run `git init`, commit baseline (`chore: init SPT <version>`) with `Spt-Version` trailer. Abort if repo exists (resumable after transient failure). Auto-adopt existing untracked mods with user confirmation. |
| `list [--all]` | Active mods from git log via `Mod:` trailer: name, version, type, SPT constraint, state, date, short hash. `--all` includes disabled and removed. |
| `files NAME` | Files in mod's install commit. |
| `remove NAME [--force] [--yes]` | Profile check (§7), then `git revert --no-edit`. Revert conflict (file modified by later mod): abort cleanly, report blocking mod. Untracked files the mod wrote are deleted (runtime data). |
| `disable NAME` / `enable NAME` | **disable** = revert with `Mod-State: disabled` trailer. **enable** = cherry-pick the youngest full-trailer commit (current version, incl. updates) with `Mod-State: enabled`. No `.disabled/` folder; all state in history. Mod state = youngest commit carrying its `Mod:` trailer. |
| `adopt PATH [--name] [--version]` | Register an existing untracked mod folder or DLL as an install commit. Metadata from `package.json`, else `--name`/`--version` override required. Makes legacy installs migratable. |
| `status` | Interpret `git status --short`: changed profiles/configs (normal post-game, hint to `snapshot`), untracked files under mod paths ("manually installed", suggest adopt). |
| `snapshot [-m MSG]` | Commit only `<runtime_dir>/user/profiles/` and `BepInEx/config/` as `snapshot: <MSG or timestamp>`. No `Mod:` trailer. |
| `check` | Profile check across all active mods and all profiles; no modifications. |
| `export [--format text|json] [--client-only]` | Mod list as text (name, version, Forge link) or JSON. `--client-only` for Fika players. |
| `import LIST [--apply]` | Parse exported list, compare to current install, show delta (missing / version mismatch / extra). `--apply` installs missing mods via Forge (Fika players sync server state with one command). |
| `history [NAME]` | Chronological commit log, optionally filtered to one mod. |

### 4c. Frontend

**Setup flow (one time, then never again):** Two steps. (1) Pick SPT directory
(native OS dialog); validation and `init` run immediately; existing mods
listed for adoption confirmation. (2) Optional: pick Forge mod source
(required to browse/install). If skipped, catalog tabs show "sync required"
placeholder. Catalog auto-syncs on startup if path and Forge are set.

**Main view:** Mod list with three tabs — **All / Installed / Available** —
plus search field and "compatible only" toggle (ON by default). Each row:
name, author, installed/latest version, type, state (active / disabled /
update available / incompatible), primary action (Install / Update /
Disable) and context menu (Enable, Remove, Files, Open in Browser). Install
shows plan confirmation (mod + deps + sizes), then progress inline.
Conflicts and profile residue appear as modal dialogs (§6/§7 messages) with
a "Force" button (`--force`).

**Side views:** **History** (commit log with file lists and undo), **Profile**
(residue check result; snapshot button), **Settings** (path; Forge status;
import/export mod list; clear cache and/or remove downloaded files).

**Not built:** Drag-and-drop, file dialogs for archives, load-order editor,
multi-instance management (one repo per path; folder picker in settings
suffices), modpack branches as a feature (git branches work; the tool just
doesn't create/switch them), automatic profile cleanup (detection only, see
§7), DLL metadata extraction (name from filename; Forge match via catalog).

## 5. `.gitignore` and Commit Format

**Whitelist pattern** (layout-aware; tool generates per-layout):

```
*
!.gitignore
!<runtime_dir>/
!<runtime_dir>/*
!<runtime_dir>/SPT_Data/**
<runtime_dir>/SPT_Data/**/cache/
<runtime_dir>/SPT_Data/**/logs/
<runtime_dir>/user/*
!<runtime_dir>/user/mods/
!<runtime_dir>/user/mods/**
!<runtime_dir>/user/profiles/
!<runtime_dir>/user/profiles/*.json
<runtime_dir>/user/profiles/backups/
!BepInEx/
BepInEx/*
!BepInEx/plugins/
!BepInEx/plugins/**
!BepInEx/config/
!BepInEx/config/**
!BepInEx/patchers/
!BepInEx/patchers/**
```

Where `<runtime_dir>` = `SPT_Runtime` (4.1+) or `SPT` (4.0.x). The `**` form
works correctly with real git (reinclusion inside ignored dirs). The SPT
program set is tracked as well — every file directly in `<runtime_dir>/`
(server and launcher executables, `.dll`, `.so`, `.json`, `.xml`) and the whole
`<runtime_dir>/SPT_Data/` tree — so an SPT update is one commit and a downgrade
one revert. Verify that `<runtime_dir>/user/logs/`,
`<runtime_dir>/user/cache/`, `<runtime_dir>/user/certs/`, cache and log
directories below `SPT_Data/`, `BepInEx/cache/`, `Logs/`, `.sptmod-*` and the
game client files remain untracked.

**Commit format:**

```
mod: SAIN 3.2.1

Mod: SAIN
Mod-Version: 3.2.1
Mod-Type: hybrid
Mod-Author: Solarint
Mod-SPT: ~4.1.0
Mod-Source: https://github.com/Solarint/SAIN/releases/download/3.2.1/SAIN-3.2.1.7z
Mod-Forge-Id: 1234
Mod-Forge-Version-Id: 56789
Mod-Guid:
Mod-State: installed
```

`Mod:` = mod name (the identifier; read/matched case-insensitively; ambiguous
names error). `Mod-Version` = semantic version (from `package.json` or
`--version`). `Mod-Type` ∈ `{server, client, hybrid}`. `Mod-Author` (from
`package.json` or fallback). `Mod-SPT` = SemVer constraint from `package.json`.
`Mod-Source` = download URL (or local filename on `install --file`).
`Mod-Forge-Id` = Forge mod id (empty if no match). `Mod-Forge-Version-Id` =
Forge version id (empty if no match). `Mod-Guid` = mod's GUID from
`package.json` (empty if none). `Mod-State` ∈ `{installed, updated,
disabled, enabled, removed}`.

On update, add `Mod-Previous-Version`. Write trailers via `git
interpret-trailers --if-exists add`; read via `git log
--format=%(trailers:...)`. Never parse trailers with regex. Reject
multiline trailer values (`\n`, `\r`) to prevent injection.

## 6. Conflict and Dependency Check on Install

Before copying each archive file: does it already exist in the game directory?

- **Tracked:** Find the mod that last touched it (`git log -1
  --format=%(trailers:key=Mod,valueonly) -- PATH`) → report "already
  provided by Mod X". Without `--force`, abort.

- **Untracked:** Report "manually installed; adopt first". Without
  `--force`, abort.

**DLL duplicates:** Same filename (e.g., `Newtonsoft.Json.dll`) under
`BepInEx/plugins/` in a different mod folder → warning (typical for
bundled shared libraries). Warn always; do not block.

**Dependencies:** Parse `package.json` → `dependencies` dict (name → SemVer
range). For each: does an active mod with that name exist, and is its
version in range? If missing: warn with list, abort unless `--force`.
`sptVersion` constraint vs. detected SPT version: mismatch is warning only
(ranges in practice are often too strict).

**Auto-snapshot:** Before any mod operation, if the tree is dirty
(modified tracked files, changed profiles/configs), commit them with
message "automatic snapshot before changing mods". Untracked files under
mod paths ("runtime data" written by the mod at runtime) are ignored —
they never block and never snapshot. `remove` and `disable` delete them
together with the mod (else the directory would resurface as foreign).

**Apply lock:** Final apply step (conflict check, copy/delete, commit)
holds an exclusive lock (`.sptmod-lock` in the game directory, ignored by
whitelist `.gitignore`) for up to 5 minutes. Read-only operations never
lock. Colliding applies report the blocking operation via progress
callback.

## 7. Profile Residue Check on Remove / Check

Before reverting a mod (§4b `remove`), or on standalone `check`: scan all
player profiles for leftovers from that mod's custom items, traders, quests.

Read the mod's install commit (from git, not the filesystem) and collect
IDs:

- **Item-template IDs:** 24-hex strings in the mod's `db/templates/items.json`
  (or equivalent catalog file).
- **Trader IDs:** Subdirectory names under `db/traders/` and trader `_id` in
  `base.json`.
- **Quest IDs:** `_id` fields in quest JSON files under `db/quests/`.

Then search all profiles for residue via field lookups (e.g.
`characters.pmc.Inventory.items[]._tpl` for item templates,
`characters.pmc.TradersInfo` keys for traders, `characters.pmc.Quests[].qid`
for quest progress) and string-search fallback. Report findings as warning
(count per profile and category); without `--force`, abort removal. With
`--force`, revert the mod; profiles unchanged (residue remains; the mod
itself is gone and can be re-installed later).

Mod database structures vary; parse heuristically and tolerantly. Parse
errors = warning, continue (do not block). Detection shows residue
categories such as item templates, traders, quests, plus string fallback;
optional automatic cleanup is planned/implemented (see ARCHITECTURE.md for
current state).

## 8. Project Structure

```
src/sptmod/
  core/
    errors.py             # typed exception hierarchy
    models.py             # dataclasses, trailer keys, Progress protocol
    game_dir.py           # layout detect, SPT version chain, process check
    repo.py               # git layer (init/commit/revert/cherry-pick/log)
    archive.py            # extract (.7z/.zip/.rar), traversal guard, anchor
    modmeta.py            # package.json parsing, trailer mapping, semver
    conflicts.py          # file/DLL/dependency/SPT-version checks
    profile/              # profile load, residue detection from db/ (a package:
                          #   nodes, ids, files, categories, scan)
    workspace.py          # staging dir, download cache, apply lock
    services/             # all user-facing operations (pure functions; a package:
                          #   common, state, gamedir, profiles, lifecycle,
                          #   install_archive, install_forge, update, catalog,
                          #   transfer, modsets, fika, spt)
    favorites.py          # favorites list, load/save
    forge/
      client.py           # Forge HTTP client (only httpx import)
      catalog/            # SQLite cache (sync/search/classify; a package:
                          #   schema, rows, store)
      cache.py            # persistent archive cache (download resume, LRU)
      download.py         # streaming downloads, cache hit/resume, verify
      resolve.py          # install plans over /mods/dependencies
  cli/                    # typer adapter (exceptions → exit codes; a package:
                          #   base, mods, repo, sources, spt, gamedir,
                          #   reports, rpc)
  server/
    rpc.py                # `sptmod rpc <name>` (stdin/stdout JSON)
    schemas/              # Pydantic schemas (contract, mirror of core; a package:
                          #   base, mods, requests, modsets, runtime)
    errors.py             # RPC error code mapping
    dump_openapi.py       # OpenAPI doc generator

src-tauri/
  src/
    rpc.rs                # Tauri command handler
    driver.rs             # spawns sptmod rpc subprocess
  tests/
    rpc_driver_tests.rs   # with mocked subprocess
    rpc_e2e_ignored.rs    # real CLI (ignored in CI)

frontend/                 # SvelteKit, Svelte 5, adapter-static, TypeScript
  src/lib/api/
    transport.ts          # call() and subscribe() (only Tauri invoke/listen)
    types.ts              # generated from openapi.json (never hand-edit)
    client.ts             # typed wrappers per RPC function
  src/routes/             # pages: / (setup or mods), /history, /profile, /settings

openapi.json              # committed contract dump; generated by dump_openapi.py
_data/mods/               # 21 real mod archives (untracked, test corpus)
```

**Hard rules:**

- `subprocess` only in `core/repo.py` (raw git, no GitPython) and, since game
  launching was added, `core/launch/`.
- `httpx` only in `core/forge/client.py`; as shipped, one client module per
  remote service: `core/forge/client.py`, `core/sptserver.py` and
  `core/sptrelease/client.py` (`core/forge/download.py` streams through the
  Forge client instead of importing httpx itself).
- No print/logging outside `cli/` and `server/`.
- All paths via `pathlib`; no Linux-only assumptions.
- Strict type annotations everywhere (`mypy --strict`).
- Forge cache is foreign state; lives in platformdirs config dir, never in
  game dir or git.
- Frontend: `adapter-static` (no SSR), generated types via `openapi-typescript`.

**Tools:** Python 3.12 + `uv`; `ruff`, `mypy --strict`, `pytest` (1194
tests, 100% coverage enforced). Frontend: `pnpm`, `vite`, `svelte-check`,
`vitest` (136 tests, 100% coverage on `src/lib/**/*.ts`).
Rust/Tauri: `cargo test` (13 + 2 ignored), `cargo llvm-cov` (91% driver).

**Dependencies:** `typer`, `rich`, `py7zr`, `rarfile`, `psutil`, `httpx`,
`platformdirs`, `pydantic` (the original spec's server and auth dependencies
are gone; see History).

## 9. Tests

**The pytest suite is the Rust-port specification** — tests are black-box,
behavior-focused; do not weaken them to fit the implementation. No real game
needed.

**Fixtures:** `fake_game_dir(tmp_path)` builds an SPT directory with
`<runtime_dir>/user/mods`, `<runtime_dir>/user/profiles/abc.json` (minimal
profile with Inventory/TradersInfo/Quests), `<runtime_dir>/SPT_Data/configs/core.json`,
`BepInEx/plugins`, `BepInEx/config`, plus dummy files at ignored paths
(`<runtime_dir>/user/logs/x.log`, `EscapeFromTarkov_Data/foo`).
`fake_game_dir_runtime` = 4.1 `SPT_Runtime/` layout. `make_archive(tmp_path,
{path: content}, format)` creates `.7z`/`.zip`/`.rar` (rar creation
untestable; skip if no backend).

**Test coverage:** 1194 collected (1193 pass, 1 skip); 100% line+branch
coverage enforced on `src/sptmod/`. 136 vitest cases (5 files): transport,
client, modfilter logic; 100% statements/branches/functions/lines on
`src/lib/**/*.ts` (minus `types.ts` and `*.test.ts`). Svelte components/routes
untested (file-based, UI-heavy). Rust: 13 driver tests (mocked subprocess),
2 e2e tests ignored (need Wry display).

**Must-have cases:**

- init: whitelist correct; ignored dummies not in commit, profile yes; second
  `init` rejected; existing mods flagged for adoption.
- archive: anchor search all variants (correct, wrapper folder, missing
  `SPT/` prefix, missing `BepInEx/`, case variations); traversal rejected;
  invalid structure with helpful error.
- install: server-only, client-only, hybrid; trailers complete; dirty tree
  rejected; game running (mocked) rejected; progress callback in order.
- conflicts: file from other mod → abort unless `--force`, name the blocking
  mod; untracked file → suggest adopt; DLL dupe → warn; missing dep → abort;
  dep wrong version → abort; `sptVersion` mismatch → warn only.
- remove: clean revert; revert conflict by later mod → clear error, `git
  status` clean after.
- disable/enable: files gone/back; `list` shows state; `list --all` shows
  removed; enable never-installed → error; double-disable → no-op hint.
- adopt: folder with `package.json`; DLL without metadata + override; then in
  `list` and `files`.
- profile: fake mod with `db/templates/items.json` and trader; profile with
  an ID in inventory → `remove` aborts, counts per category; `--force`
  reverts; `check` finds same, no change; bad db JSON → warn, continue.
- export/import: roundtrip; import vs. different state → correct delta;
  `--apply` installs missing via mocked Forge.
- snapshot: only profiles/configs, mod files untouched.
- forge/client (all vs. respx mocks, never real Forge): auth header set;
  token-bucket throttle; 429 → backoff/retry; pagination complete; network
  error → typed `ForgeUnavailableError`, cache still usable.
- forge/catalog: full sync fills SQLite; delta sync updates only changed;
  search by name/author; compatibility filter vs. SPT version; browse joins
  cache+history correctly (installed/available/update).
- forge/download: streaming with progress; `content_length` mismatch →
  error, temp deleted; filename from Content-Disposition, URL fallback.
- forge/resolve: plan includes mod + all missing deps in install order;
  already-installed deps skipped; unresolvable → error with name.
- install (Forge path): e2e with mocked Forge and fake archive: commit has
  `Mod-Forge-Id`, `Mod-Forge-Version-Id`, `Mod-Source`=URL; deps as separate
  commits prior.
- update: new version replaces old files; removed old files gone;
  `BepInEx/config/` untouched; `Mod-Previous-Version` trailer set.
- RPC: every service function tested; error → correct code+details; OpenAPI
  schema generated and complete; contract parity tests pass.
- Frontend: `transport.ts` with mocked invoke; `client.ts` type-correct to
  generated types; tab filter logic (all/installed/available × compatible)
  pure-function tested.

## 10. Dependency Order

No timeline, only dependency order — each step is testable before the next.

1. Core package structure, pure logic, typed exceptions.
2. `repo.py` git layer + tests (the module Rust port will replace).
3. `archive.py` with anchor search and variants + tests.
4. Install core, `init`, `list`, `files`, `remove`, `history`, `install
   --file` — CLI.
5. Conflict checks, dependencies from `package.json`, profile residue check.
6. `disable`/`enable`, `adopt`, `status`, `snapshot`, `check`,
   `export`/`import`.
7. Forge module: client → catalog → cache → download → resolve (all mocked).
   Then `sync`, `search`, `browse`, `install` (Forge), `update`.
8. RPC adapter (`sptmod rpc <name>` via stdio), Pydantic schemas, OpenAPI
   dump.
9. Tauri desktop app, Rust `rpc.rs` driver.
10. Svelte frontend: transport layer, setup flow, mods view (three tabs),
    history, profile, settings.

**References for implementation questions:**
- SPT structure: https://github.com/sp-tarkov/wiki
- Existing manager reference: https://github.com/Nevek20/SPT_Mod_Manager
- Forge API (verified): `docs/FORGE_API.md` (do not cross-reference old docs)

## History

This document was the original German specification ("Version 3", 2024). It
has been rewritten in English as the current product specification. Since the
original: **Tauri RPC (v2) replaced FastAPI/SSE** — the desktop app invokes
`sptmod rpc <name>` as a subprocess, marshaling JSON over stdin/stdout; no
HTTP server. **Forge API became public and read-only** — no login, no Bearer
tokens, no keyring token storage; `https://sp-mod.com/api/v0` (override via
`SPTMOD_FORGE_URL`). **Game layouts** now auto-detect `SPT_Runtime/` (4.1+)
vs. `SPT/` (4.0); `SPT_Runtime/` is preferred when both exist. **Auto-snapshot
on dirty tree** replaces blocking — changed profiles/configs auto-commit
before operations; untracked mod files are never blocked (runtime data).
**Cherry-pick on enable** instead of literal original-commit replay.
**Persistent download cache** with resume (`Range` headers), LRU eviction,
and 20 GiB default cap (removable via Settings). **Apply lock** (`.sptmod-lock`)
gates final operations, serializes applies, waits up to 5 minutes with
progress feedback. **Favorites** (`favorites.json` in config dir, survives
`clear_catalog_cache`) map Forge mod IDs globally. **Description cache**
(in catalog SQLite) stores HTML-stripped text with timestamps for server-side
dedup. **Profile residue detection** categorizes custom items, traders, quests
per mod; removal without `--force` aborts if residue found; optional cleanup
planned (separate team). **Inventory field fix:** `_tpl`, not `tpl`. **SPT program files are
tracked** — the whitelist covers `<runtime_dir>/*` and
`<runtime_dir>/SPT_Data/**`; a repository created with the older whitelist gets
its `.gitignore` rewritten on the next status and the newly visible files are
reported as changed game files, which the next mod operation snapshots.
