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

## Install

Download the package for your system from this repository's releases page:

| System | Package | Updates itself |
|---|---|---|
| Linux | `.AppImage` | yes |
| Linux | `.deb` | no — install the new version from the releases page |
| Windows | `.msi` | yes |
| Windows | NSIS `-setup.exe` | yes |

Requirements:

- `git` on the `PATH`. On Windows, install [Git for Windows](https://git-scm.com/download/win) and
  keep the setup option *"Git from the command line and also from 3rd-party software"*. The `.deb`
  package declares `git` as a dependency.
- An SPT installation (version 4.0+) with either `SPT/` or `SPT_Runtime/` directory and
  `BepInEx/` directory.
- Optional: `unrar` or `bsdtar` (libarchive) on the `PATH` — needed only for mods that ship as
  `.rar`.

The Windows installers are not code-signed. SmartScreen shows *"Windows protected your PC"* (click
*More info* → *Run anyway*), and some antivirus products flag the download; both are the expected
reaction to an unsigned installer.

Every push is also built: the CI run's `bundle-ubuntu-latest` and `bundle-windows-latest` artifacts
hold the packages of that commit, kept for seven days. They are unsigned test builds and cannot
update themselves.

### Updates

*Settings → Updates* shows the installed version and a *Check for updates* button that downloads and
installs a new release; afterwards the same section offers a restart into the new version. The app
also checks once on startup and then shows a line above the content that links to those settings — a
startup check that fails stays silent.

### From source

Requires Python 3.12 or newer and [uv](https://docs.astral.sh/uv/). From a clone of this repository:

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

Build and run the Tauri v2 app (build the sidecar once first, see *Development*):

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

From the *Profiles* tab, you can launch the game with any readable profile. sptmod starts the SPT
server, waits until it is ready, signs you in, and launches the game client. The *Mods* view shows
a banner while the game is running and locks mod changes until you close it. The *Settings* page
has a checkbox to control whether the server shuts down when the game closes (default on).

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
| `sptmod spt list` | List the SPT versions the game directory can change to, with the installed Escape from Tarkov build. |
| `sptmod spt update VERSION [--yes]` | Change the game directory to a newer SPT version. |
| `sptmod spt downgrade [VERSION] [--yes]` | Go back to an older SPT version, or undo the last SPT change when VERSION is omitted. |
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

The app ships the Python core as a PyInstaller sidecar, declared in `src-tauri/tauri.conf.json` as a
bundle resource. `cargo build` fails with a missing-resource error until that sidecar has been built
once:

```sh
uv sync --group build
uv run pyinstaller packaging/sptmod.spec --noconfirm --distpath src-tauri/sidecar \
  --workpath packaging/build
```

It lands in `src-tauri/sidecar/sptmod/` (untracked) and is picked up by both `cargo tauri dev` and a
bundle build. The desktop app prefers the binary named by `SPTMOD_CLI`, then this sidecar, then
`sptmod` on the `PATH`.

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

## Release

Bump the version in **both** `pyproject.toml` (`[project].version`) and `src-tauri/Cargo.toml`
(`[package].version`); `tests/test_version_agreement.py` fails when they disagree.
`src-tauri/tauri.conf.json` deliberately carries no `version` key and inherits the crate's.

Then tag and push:

```sh
git tag vX.Y.Z
git push origin vX.Y.Z
```

`.github/workflows/release.yml` builds Linux and Windows one after the other and uploads the
installers plus the updater manifest `latest.json` into a **draft** GitHub release. Publish that
draft — the updater endpoint reads the latest published release.

The workflow refuses to build when the one-time setup below is missing: it stops while
`plugins.updater.endpoints` still names `OWNER/REPO` or `plugins.updater.pubkey` is still the
placeholder key, and it stops when the tag is not `v` plus the `pyproject.toml` version.

### One-time setup before the first release

Update packages must be signed, otherwise installed apps reject them:

```sh
cargo tauri signer generate -w ~/.tauri/sptmod.key    # empty password
```

- Keep `~/.tauri/sptmod.key` offline and **never commit it**. Without that key no future release can
  be signed for the apps already installed, and they can never update again.
- Paste the content of `~/.tauri/sptmod.key.pub` into `plugins.updater.pubkey` in
  `src-tauri/tauri.conf.json`, replacing the placeholder key committed here.
- Replace `OWNER/REPO` in `plugins.updater.endpoints` with the GitHub repository.
- Add two repository secrets: `TAURI_SIGNING_PRIVATE_KEY` (the content of the key file) and
  `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` (empty).
