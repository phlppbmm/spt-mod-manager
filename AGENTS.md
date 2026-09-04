# Agent Instructions

This project uses **bd** (beads) for issue tracking. Run `bd prime` for full workflow context.

> **Architecture in one line:** Issues live in a local Dolt database
> (`.beads/dolt/`); cross-machine sync uses `bd dolt push/pull` (a
> git-compatible protocol), stored under `refs/dolt/data` on your git
> remote — separate from `refs/heads/*` where your code lives.
> `.beads/issues.jsonl` is a passive export, not the wire protocol.
>
> See [SYNC_CONCEPTS.md](https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md)
> for the one-screen overview and anti-patterns (don't treat JSONL as the
> source of truth; don't `bd import` during normal operation; don't
> reach for third-party Dolt hosting before trying the default).

## Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work atomically
bd close <id>         # Complete work
bd dolt push          # Push beads data to remote
```

## Non-Interactive Shell Commands

**ALWAYS use non-interactive flags** with file operations to avoid hanging on confirmation prompts.

Shell commands like `cp`, `mv`, and `rm` may be aliased to include `-i` (interactive) mode on some systems, causing the agent to hang indefinitely waiting for y/n input.

**Use these forms instead:**
```bash
# Force overwrite without prompting
cp -f source dest           # NOT: cp source dest
mv -f source dest           # NOT: mv source dest
rm -f file                  # NOT: rm file

# For recursive operations
rm -rf directory            # NOT: rm -r directory
cp -rf source dest          # NOT: cp -r source dest
```

**Other commands that may prompt:**
- `scp` - use `-o BatchMode=yes` for non-interactive
- `ssh` - use `-o BatchMode=yes` to fail instead of prompting
- `apt-get` - use `-y` flag
- `brew` - use `HOMEBREW_NO_AUTO_UPDATE=1` env var

## Project Context

**Read `docs/ARCHITECTURE.md` first in any fresh session** — it is the
authoritative current state, including every verified deviation from the
original spec. `docs/FORGE_API.md` is the verified API contract;
`docs/HANDOFF.md` is the product specification (rewritten in English from
the original handoff; its History section lists what changed).

sptmod: a git-based mod manager for Single Player Tarkov. Python core +
typer CLI + Tauri v2 desktop app (RPC) + Svelte 5 frontend (stage 1); stage 2
will port core+CLI to Rust — the pytest suite is the specification for that
port.

## Build & Test

```bash
uv sync                              # Python env
uv run pytest                        # full suite (1194 collected)
uv run pytest --cov                  # with coverage gates (line & branch 100%)
uv run ruff check src
uv run mypy --strict src
cd frontend && pnpm install && pnpm check && pnpm test && pnpm build
cd frontend && pnpm vitest run --coverage  # frontend coverage (v8, 100%)
cargo build && cargo test            # Rust (Tauri, src-tauri/)
cd src-tauri && cargo llvm-cov       # Rust coverage (70.54% lines)

# after changing service functions or request/response schemas:
uv run python -m sptmod.server.dump_openapi openapi.json
cd frontend && pnpm gen:api          # regenerates src/lib/api/types.ts

# run the app (Tauri desktop):
uv tool install --editable .         # install sptmod CLI
cargo tauri dev                      # hot reload during development
```

## Architecture in five lines

- `src/sptmod/core/` is pure logic: typed exceptions, dataclasses, injected
  progress callbacks; it knows neither CLI nor HTTP.
- `subprocess` ONLY in `core/repo.py` and `core/launch.py`; `httpx` ONLY in `core/forge/client.py` and `core/sptserver.py`.
- One contract, four transports: service function ↔ `sptmod rpc <name>` ↔
  Tauri `#[tauri::command] rpc()` ↔ client.ts wrapper — guarded by
  route-per-function and schema-parity tests.
- `frontend/src/lib/api/transport.ts` is the only invoke/listen site;
  `types.ts` is generated, never hand-edited.
- Git never surfaces in user-facing language (auto-snapshot handles dirty
  profiles; errors speak plain language).

## Conventions & hard-won rules

- Tests are the Rust-port spec: black-box, behavior-focused; don't weaken
  them to make an implementation fit.
- The Forge moved to `https://sp-mod.com/api/v0` (no auth exists; env
  override `SPTMOD_FORGE_URL`). Never hit the real API from tests (respx).
- Game layouts: `SPT_Runtime/` (4.1+) and `SPT/` (4.0) via
  `game_dir.detect_layout`; never hardcode `SPT/` paths.
- Mod identity: the install-commit `Mod:` trailer (from the mod's own
  package.json) ≠ the Forge display name; link rows by Forge id.
- Catalog cache is disposable; schema evolves via the ALTER list in
  `catalog._MOD_COLUMN_MIGRATIONS`.
- Line length 88 (ruff E501) — including docstrings.
- Real-mod corpus for manual verification lives untracked in `_data/mods/`.
- Tests exercise only public entry points and observable effects; never patch
  or call private names (service functions, public methods, CliRunner, `sptmod rpc`, TS exports).
- Only mocked boundaries: HTTP (respx), subprocess (stub binary), environment
  (PATH, HOME, XDG_*), filesystem conditions. Public keyword-only seams for
  testability: `locate_directory(roots=)`, `game_lock(timeout=, poll_interval=)`.
- Sibling cases parametrized with descriptive ids; coverage exclusions only via
  the two `exclude_also` patterns in pyproject (sys.platform, TYPE_CHECKING).
  No `# pragma: no cover`; unreachable defensive code is deleted.
- A strict `xfail` may pin a known production bug only with a bead reference.
