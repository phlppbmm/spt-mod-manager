# sptmod

A mod manager for SPT (Single Player Tarkov) that keeps the whole game directory in a git
repository, so every install, update, removal and profile change is a commit you can inspect
and revert.

## ⚠️ Prototype — back up your game directory first

sptmod is an early prototype. It writes into your SPT installation, creates commits and reverts
files. **Make a full backup of the game directory before you point sptmod at it**, and do not
run it against an installation you cannot afford to lose.

## What it is

- **Git-based.** `sptmod init` turns the SPT installation into a repository with a whitelist
  `.gitignore` that includes `<runtime>/user/mods/**`,
  `<runtime>/user/profiles/*.json`, `BepInEx/plugins/**`, `BepInEx/config/**`,
  and `BepInEx/patchers/**` (where `<runtime>` is detected per game version:
  `SPT_Runtime/` for 4.1+ or `SPT/` for 4.0).
- **Every install is revertible.** Each mod is one commit with `Mod-*` trailers; removing or
  disabling a mod is a revert, enabling it again is a cherry-pick. Nothing is moved into a
  `.disabled` folder, the history is the state.
- **Profiles are versioned along.** `sptmod snapshot` commits the player profiles and the
  BepInEx configs, and `sptmod check` reports mod content (items, traders, quests) that a
  profile still references before you remove a mod.
- **Mod lists are text.** `sptmod export` writes the active mods as text or JSON, `sptmod
  import` diffs such a list against your installation — useful to share a Fika server's mod set.
- **Linux and Windows.** No shell-outs beyond git, all paths via `pathlib`.

## Requirements

- Python 3.12 or newer and [uv](https://docs.astral.sh/uv/)
- `git` on the `PATH`
- An SPT installation (version 4.0+) with either `SPT/` or `SPT_Runtime/` directory and
  `BepInEx/` directory

## Install

From a clone of this repository:

```sh
uv sync
uv run sptmod --help
```

To get an `sptmod` command outside the clone:

```sh
uv tool install .
```

## First run (CLI)

All local commands operate on the current working directory, so run them from inside the game
directory:

```sh
cd /path/to/SPT
sptmod init            # detect SPT, write .gitignore, create the first commit
sptmod sync            # fill the local Forge catalog cache
sptmod browse --compatible
sptmod install SAIN                            # from the Forge catalog
sptmod install --file ~/Downloads/SAIN-3.2.1.7z  # from a local archive
sptmod update --check
sptmod list
```

`sptmod status` explains what changed since the last commit, `sptmod history` shows the commit
log, `sptmod files NAME` the files of a single mod.

## First run (Tauri desktop app)

Build and run the Tauri v2 app:

```sh
uv tool install --editable .         # install sptmod CLI
cargo tauri dev                       # dev mode with hot reload
```

Or build a distributable binary:

```sh
cargo build
cargo tauri build --debug             # or `--release`
```

The app uses native dialogs (folder picking, file operations) and communicates with `sptmod rpc`
commands. The UI asks for the game directory once, runs `init` (with a list of pre-existing mods
to adopt), then shows the mod list with the tabs *All*, *Installed*, *Not installed*, plus the
side views *History*, *Profiles* and *Settings*. The import section of *Settings* takes a
pasted mod list as well as a local file path.

## Migrating an existing modded installation

`sptmod init` finds mods that are already installed and offers to adopt each one, so they
become regular install commits:

```sh
cd /path/to/SPT
sptmod init          # asks per pre-existing mod
sptmod init --yes    # adopts all of them without asking
```

Mods that were not picked up — a loose DLL, an unusual folder layout — can be added later.
`sptmod status` groups untracked files under the mod paths as "manually installed" and names
them:

```sh
sptmod adopt BepInEx/plugins/SomePlugin.dll --name SomePlugin --version 1.0.0
sptmod adopt SPT/user/mods/some-server-mod
```

Metadata comes from `package.json` where the mod has one; otherwise pass `--name` and
`--version`.

## Forge

The [Forge](https://sp-mod.com) API v0 is **public and read-only** — no account, no
login, no token. sptmod only reads from it: catalog listings, versions, dependencies and mod
downloads. Requests are throttled client-side and back off on HTTP 429.

`sptmod sync` mirrors the catalog into a SQLite cache in the sptmod configuration directory of
the current user (`forge-catalog.db`, e.g. `~/.config/sptmod/` on Linux and
`%LOCALAPPDATA%\sptmod\` on Windows). The cache is never written into the game directory. To
reset it, delete the file and run `sptmod sync` again. Mod favorites
(`favorites.json`) are also stored in the configuration directory and persist across
cache resets and game directory switches.

## Commands

| Command | What it does |
|---|---|
| `sptmod init [PATH] [--yes] [--spt-version]` | Validate the SPT directory, write the whitelist `.gitignore`, create the initial commit, adopt pre-existing mods. |
| `sptmod install MOD [--version] [--force] [--yes]` | Install a mod from the Forge catalog with its missing dependencies, one commit each. |
| `sptmod install --file ARCHIVE [--name] [--version] [--force]` | Install mods from local `.7z`/`.zip`/`.rar` archives. |
| `sptmod update [NAME...] [--check] [--force] [--yes]` | Update installed mods to the versions Forge recommends; `--check` only reports them. |
| `sptmod list [--all]` | Active mods; `--all` includes disabled and removed ones. |
| `sptmod files NAME` | Files belonging to a mod. |
| `sptmod remove NAME [--force] [--yes]` | Revert the mod's files after the profile check. |
| `sptmod disable NAME` / `sptmod enable NAME` | Take a mod out of the game directory and bring it back. |
| `sptmod adopt PATH [--name] [--version]` | Take a manually installed mod folder or DLL into the repository. |
| `sptmod status` | Interpret the working tree: changed profiles, changed configs, manually installed mods. |
| `sptmod snapshot [-m MSG]` | Commit the player profiles and BepInEx configs. |
| `sptmod check` | Report mod content that the player profiles still reference. |
| `sptmod history [NAME]` | Commit history, optionally filtered to one mod. |
| `sptmod export [--format text\|json] [--client-only]` | Write the mod list; `--client-only` keeps the mods a Fika client needs. |
| `sptmod import LIST [--apply]` | Diff an exported list against the installation. |
| `sptmod sync [--full]` | Refresh the local Forge catalog cache. |
| `sptmod search QUERY [--compatible]` | Search the cached catalog. |
| `sptmod browse [--installed\|--available\|--all] [--compatible]` | List the cached catalog with the installed state. |
| `sptmod version` | Print the version. |

## Known limitation

Metadata is read from a mod's `package.json`. Client-only mods that ship a bare DLL have none,
and sptmod does not read version or GUID out of the assembly, so `sptmod install --file` records
them without a version unless you pass `--version`.

## Development

Python:

```sh
uv sync
uv run ruff check
uv run mypy --strict src
uv run pytest
```

Rust (Tauri desktop app):

```sh
cargo build
cargo test
cargo tauri dev                       # dev mode with hot reload
```

Frontend (SvelteKit, Svelte 5 runes, TypeScript, static adapter):

```sh
cd frontend
pnpm install
pnpm dev        # dev server on :5173
pnpm check      # svelte-check
pnpm test       # vitest
pnpm build
```

The API types in `frontend/src/lib/api/types.ts` are generated from the RPC schemas; regenerate
them with `pnpm gen:api` after changing service functions or request/response models, and keep
`invoke` and `listen` confined to `frontend/src/lib/api/transport.ts`.
